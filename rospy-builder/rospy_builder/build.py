#!/usr/bin/env python
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile

import click
import git
import yaml
from dataclasses import dataclass, field
from typing import List, Optional


def normalize(name: str) -> str:
    # https://www.python.org/dev/peps/pep-0503/
    return re.sub(r"[-_.]+", "-", name).lower()


def tarsum(file_name: pathlib.Path) -> str:
    tar = tarfile.open(mode="r|*", fileobj=file_name.open("rb"))
    chunk_size = 512 * 1024
    h = hashlib.sha256()
    for member in tar:
        if not member.isfile():
            continue
        f = tar.extractfile(member)
        data = f.read(chunk_size)
        while data:
            h.update(data)
            data = f.read(chunk_size)
    return h.hexdigest()


def download_from_github(
    dest_dir: pathlib.Path, repo: str, ver: str
) -> pathlib.Path:
    url = f"https://github.com/{repo}/archive/{ver}.zip"
    zip_file = dest_dir / f'{repo.replace("/", "_")}_{ver}.zip'
    if not zip_file.exists():
        u = urlopen(url)
        with open(zip_file, "wb") as outs:
            block_sz = 8192
            while True:
                buf = u.read(block_sz)
                if not buf:
                    break
                outs.write(buf)
    return zip_file


def unzip(
    zip_file: pathlib.Path,
    dest_dir: pathlib.Path,
    sub_dir: Optional[pathlib.Path] = None,
) -> None:
    with open(zip_file, "rb") as f:
        zipfp = zipfile.ZipFile(f)
        for zip_file_name in zipfp.namelist():
            original = pathlib.Path(zip_file_name)
            name = pathlib.Path(*original.parts[1:])
            if sub_dir:
                try:
                    name.relative_to(sub_dir)
                except ValueError:
                    continue
                fname = dest_dir / pathlib.Path(
                    *name.parts[len(sub_dir.parts) :]
                )
            else:
                fname = dest_dir / name
            data = zipfp.read(zip_file_name)
            if zip_file_name.endswith("/"):
                if not fname.exists():
                    fname.mkdir(parents=True)
            else:
                fname.write_bytes(data)


def build_package(
    package_dir: pathlib.Path,
    dest_dir: pathlib.Path,
    build_py2_binary: bool = False,
    release_version: Optional[str] = None,
    requires: List[str] = [],
    unrequires: List[str] = [],
    compare: bool = True,
) -> None:
    setup_code = (package_dir / "setup.py").read_text()

    # Patch catkin_pkg.python_setup.generate_distutils_setup
    # 1. to replace 'Requires' by 'Requires-Dist'
    # 2. modify install_requires and version
    # 3. add packages for ROS message
    # https://www.python.org/dev/peps/pep-0314/#requires-multiple-use
    # https://packaging.python.org/specifications/core-metadata/
    import setuptools  # NOQA
    import catkin_pkg.python_setup

    def patched_generate_distutils_setup(**kwargs):
        new_kwargs = catkin_pkg.python_setup.original_generate_distutils_setup(
            **kwargs
        )
        if "requires" in new_kwargs:
            new_kwargs["install_requires"] = sorted(
                list(new_kwargs["requires"])
            )
            del new_kwargs["requires"]
        if len(requires) > 0 or len(unrequires) > 0:
            new_kwargs["install_requires"] = sorted(
                set(new_kwargs.get("install_requires", [])) - set(unrequires)
                | set(requires)
            )
        if (
            "install_requires" in new_kwargs
            and "genpy" in new_kwargs["install_requires"]
        ):
            new_kwargs["install_requires"].remove("genpy")
            new_kwargs["install_requires"].append("genpy<2000")
            new_kwargs["install_requires"].sort()
        if "packages" in new_kwargs:
            packages = new_kwargs["packages"]
            for subtype in ("msg", "srv"):
                sub_package = package_dir.name + "." + subtype
                if (
                    package_dir / subtype
                ).exists() and sub_package not in packages:
                    packages.append(sub_package)
        if release_version is not None:
            new_kwargs["version"] = release_version
        return new_kwargs

    catkin_pkg.python_setup.original_generate_distutils_setup = (
        catkin_pkg.python_setup.generate_distutils_setup
    )
    catkin_pkg.python_setup.generate_distutils_setup = (
        patched_generate_distutils_setup
    )

    try:
        cwd = os.getcwd()
        original_argv = sys.argv
        dest_package_dir = dest_dir / normalize(package_dir.name)
        dest_package_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(package_dir)
        # build source package
        sys.argv = ["", "sdist"]
        exec(setup_code, globals())
        # check before copy
        tar_file = next((package_dir / "dist").glob("*.tar.gz"))
        if (dest_package_dir / tar_file.name).exists():
            print(dest_package_dir / tar_file.name)
            if compare:
                digest0 = tarsum(tar_file)
                digest1 = tarsum(dest_package_dir / tar_file.name)
                if digest0 != digest1:
                    print(
                        "Hash is same! Remove or change the version."
                        f"{tar_file.name}"
                    )
                    shutil.copy(tar_file, cwd + "/" + tar_file.name + ".new")
                    shutil.copy(
                        dest_package_dir / tar_file.name,
                        cwd + "/" + tar_file.name + ".org",
                    )
                    sys.exit(1)
                else:
                    print(f"content is not changed: {tar_file.name}")
            return
        print("copy")
        shutil.copy(tar_file, dest_package_dir)
        # if it's updated build the binary package
        sys.argv = ["", "bdist_wheel", "--universal"]
        exec(setup_code, globals())
        if build_py2_binary:
            # TODO: find a better way
            subprocess.call(["python2", "setup.py", "bdist_wheel"])
        for wheel in (package_dir / "dist").glob("*.whl"):
            shutil.copy(wheel, dest_package_dir)
    finally:
        sys.argv = original_argv
        os.chdir(cwd)
        catkin_pkg.python_setup.generate_distutils_setup = (
            catkin_pkg.python_setup.original_generate_distutils_setup
        )


def generate_rosmsg_from_action(
    dest_msg_dir: pathlib.Path, source_action_dir: pathlib.Path
) -> None:
    files = source_action_dir.glob("*.action")
    for action in files:
        dest_msg_dir.mkdir(parents=True, exist_ok=True)
        name = action.name[:-7]
        # parse
        parts = [[]]
        for l in action.read_text().split("\n"):
            if l.startswith("---"):
                parts.append([])
                continue
            parts[-1].append(l)
        parts = ["\n".join(p) for p in parts]
        assert len(parts) == 3
        (dest_msg_dir / (name + "Goal.msg")).write_text(parts[0])
        (dest_msg_dir / (name + "Result.msg")).write_text(parts[1])
        (dest_msg_dir / (name + "Feedback.msg")).write_text(parts[2])
        (dest_msg_dir / (name + "Action.msg")).write_text(
            f"""{name}ActionGoal action_goal
{name}ActionResult action_result
{name}ActionFeedback action_feedback
"""
        )
        (dest_msg_dir / (name + "ActionGoal.msg")).write_text(
            f"""Header header
actionlib_msgs/GoalID goal_id
{name}Goal goal
"""
        )
        (dest_msg_dir / (name + "ActionResult.msg")).write_text(
            f"""Header header
actionlib_msgs/GoalStatus status
{name}Result result
"""
        )
        (dest_msg_dir / (name + "ActionFeedback.msg")).write_text(
            f"""Header header
actionlib_msgs/GoalStatus status
{name}Feedback feedback
"""
        )


def generate_package_from_rosmsg(
    package_dir: pathlib.Path,
    package: str,
    version: Optional[str] = None,
    search_root_dir: Optional[pathlib.Path] = None,
    src_dir: Optional[pathlib.Path] = None,
    release_version: Optional[str] = None,
) -> None:
    import genpy.generator
    import genpy.genpy_main

    search_dir = {package: [package_dir / "msg"]}
    if search_root_dir is not None:
        for msg_dir in search_root_dir.glob("**/msg"):
            p = msg_dir.parent.name
            if p not in search_dir:
                search_dir[p] = []
            search_dir[p].append(msg_dir)
    if src_dir is None:
        dest_package_dir = package_dir / package
    else:
        dest_package_dir = package_dir / src_dir / package
    print(dest_package_dir)
    for gentype in ("msg", "srv"):
        files = (package_dir / gentype).glob(f"*.{gentype}")
        if files:
            if gentype == "msg":
                generator = genpy.generator.MsgGenerator()
            elif gentype == "srv":
                generator = genpy.generator.SrvGenerator()
            ret = generator.generate_messages(
                package, files, dest_package_dir / gentype, search_dir
            )
            if ret:
                raise RuntimeError(
                    "Failed to generate python files from msg files."
                )
            genpy.generate_initpy.write_modules(dest_package_dir / gentype)
        if not (dest_package_dir / "__init__.py").exists():
            genpy.generate_initpy.write_modules(dest_package_dir)
    if not (package_dir / "setup.py").exists():
        if version is None:
            version = "0.0.0"
            package_xml = package_dir / "package.xml"
            if package_xml.exists():
                v = re.search(
                    "<version>(.*)</version>", package_xml.read_text()
                )
                if v:
                    version = v.group(1)
        if release_version is not None:
            version = release_version
        (package_dir / "setup.py").write_text(
            f"""from setuptools import find_packages, setup
setup(name=\'{package}\', version=\'{version}\', packages=find_packages(),
      install_requires=[\'genpy<2000\'])"""
        )


def build_package_from_github_package(
    build_dir: pathlib.Path,
    dest_dir: pathlib.Path,
    repository: str,
    version: str,
    sub_dir: Optional[pathlib.Path] = None,
    src_dir: Optional[pathlib.Path] = None,
    release_version: Optional[str] = None,
    requires: List[str] = [],
    unrequires: List[str] = [],
    compare: bool = True,
) -> None:
    if sub_dir:
        package = sub_dir.name
    else:
        package = repository.split("/")[1]
    package_dir = build_dir / package
    zipfile = download_from_github(build_dir, repository, version)
    unzip(zipfile, package_dir, sub_dir)
    if src_dir is not None and (
        (package_dir / "msg").exists() or (package_dir / "srv").exists()
    ):
        generate_package_from_rosmsg(
            package_dir,
            package,
            None,
            build_dir,
            src_dir,
            release_version=release_version,
        )
    build_package(
        package_dir=package_dir,
        dest_dir=dest_dir,
        release_version=release_version,
        requires=requires,
        unrequires=unrequires,
        compare=compare,
    )


def build_package_from_github_msg(
    build_dir: pathlib.Path,
    dest_dir: pathlib.Path,
    repository: str,
    version: str,
    sub_dir: Optional[pathlib.Path] = None,
    release_version: Optional[str] = None,
    requires: List[str] = [],
    unrequires: List[str] = [],
    compare: bool = True,
) -> None:
    if sub_dir:
        package = sub_dir.name
    else:
        package = repository.split("/")[1]
    package_dir = build_dir / package
    zipfile = download_from_github(build_dir, repository, version)
    if sub_dir is None:
        sub_dir = pathlib.Path()
    unzip(zipfile, package_dir / "msg", sub_dir / "msg")
    unzip(zipfile, package_dir / "srv", sub_dir / "srv")
    unzip(zipfile, package_dir / "action", sub_dir / "action")
    generate_rosmsg_from_action(package_dir / "msg", package_dir / "action")
    generate_package_from_rosmsg(
        package_dir,
        package,
        version,
        search_root_dir=build_dir,
        release_version=release_version,
    )
    build_package(
        package_dir=package_dir,
        dest_dir=dest_dir,
        release_version=release_version,
        requires=requires,
        unrequires=unrequires,
        compare=compare,
    )


def build_package_from_local_package(
    build_dir: pathlib.Path,
    dest_dir: pathlib.Path,
    src_dir: pathlib.Path,
    build_py2_binary: bool = False,
) -> None:
    package = src_dir.name
    package_dir = build_dir / package
    shutil.rmtree(package_dir, ignore_errors=True)
    shutil.copytree(src_dir, package_dir)
    build_package(
        package_dir=package_dir,
        dest_dir=dest_dir,
        build_py2_binary=build_py2_binary,
    )


def generate_package_index(
    dest_dir: pathlib.Path,
    package_name: str,
    remote: Optional[git.remote.Remote] = None,
) -> bool:
    normalized_package_name = normalize(package_name)
    dest_package_dir = dest_dir / normalized_package_name
    files = {}
    for f in dest_package_dir.glob("*.tar.gz"):
        files[f.name] = f.name
    for f in dest_package_dir.glob("*.whl"):
        files[f.name] = f.name
    if remote is not None:
        url = pathlib.Path(remote.url)
        raw_url = (
            pathlib.Path("github.com") / url.parent.name / url.stem / "raw"
        )
        for branch in ("Darwin",):
            if normalized_package_name in remote.refs[branch].commit.tree:
                for f in (
                    remote.refs[branch]
                    .commit.tree[normalized_package_name]
                    .blobs
                ):
                    if f.name not in files:
                        print(f.name)
                        files[f.name] = "https://" + str(
                            raw_url / branch / normalized_package_name / f.name
                        )
    files_list = "".join(
        [f'<a href="{files[f]}">{f}</a><br>\n' for f in sorted(files.keys())]
    )
    (dest_package_dir / "index.html").write_text(
        f"<!DOCTYPE html><html><body>\n{files_list}</body></html>"
    )
    return len(files) != 0


def generate_index(dest_dir: pathlib.Path,) -> None:
    packages = []
    for package_dir in dest_dir.glob("*"):
        if package_dir.is_dir():
            packages.append(package_dir.name)
    package_list = "".join(
        [
            f'<a href="{re.sub(r"[-_.]+", "-", p).lower()}/">{p}</a><br>\n'
            for p in sorted(packages)
            if p != ".git"
        ]
    )
    (dest_dir / "index.html").write_text(
        f"<!DOCTYPE html><html><body>\n{package_list}</body></html>"
    )


@dataclass
class PackageInfo:
    name: str
    path: Optional[str] = None
    repository: Optional[str] = None
    version: Optional[str] = None
    build_py2_binary: Optional[bool] = False
    release_version: Optional[str] = None
    type: Optional[str] = None
    src: Optional[str] = None
    requires: Optional[List[str]] = field(default_factory=list)
    unrequires: Optional[List[str]] = field(default_factory=list)
    skip_compare: Optional[bool] = False


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx, package_list: str = None) -> None:
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@cli.command(help="build packages")
@click.option(
    "-l",
    "--list",
    "package_list",
    type=click.Path(exists=True),
    help="path to packages.yaml",
    default=os.getcwd() + "/packages.yaml",
)
@click.option(
    "-i",
    "--index",
    "index_dir",
    type=click.Path(),
    help="path where generate index",
    default=os.getcwd() + "/index",
)
@click.option(
    "--no-index",
    type=bool,
    help="do not generate index",
    default=False,
    is_flag=True,
)
@click.argument("target", required=False, type=str)
def build(
    target: Optional[str], package_list: str, index_dir: str, no_index: bool,
) -> None:
    index_dir_path = pathlib.Path(index_dir)
    with open(package_list) as f:
        packages_dict = yaml.safe_load(f)
    packages: List[PackageInfo] = []
    for package in packages_dict:
        packages.append(PackageInfo(**package))
    if target is not None:
        if target not in [p.name for p in packages]:
            print(f"{target} is not find in {package_list}")
            sys.exit(1)
    tmp = pathlib.Path(tempfile.mkdtemp())
    origin = None
    try:
        repo = git.Repo()
        origin = repo.remotes.origin
        origin.fetch()
    except git.exc.InvalidGitRepositoryError:
        print("Not a git directory. Will not include other arch binaries")
    try:
        for package in packages:
            if target is not None and target != package.name:
                continue
            print(package.name)
            if package.repository is None:
                build_package_from_local_package(
                    build_dir=tmp,
                    dest_dir=index_dir_path,
                    src_dir=pathlib.Path(package.path),
                    build_py2_binary=package.build_py2_binary,
                )
            elif package.version is not None:
                path = (
                    pathlib.Path(package.path)
                    if package.path is not None
                    else None
                )
                src = (
                    pathlib.Path(package.src)
                    if package.src is not None
                    else None
                )
                if package.type is None:
                    build_package_from_github_package(
                        build_dir=tmp,
                        dest_dir=index_dir_path,
                        repository=package.repository,
                        version=package.version,
                        sub_dir=path,
                        src_dir=src,
                        release_version=package.release_version,
                        requires=package.requires,
                        unrequires=package.unrequires,
                        compare=not package.skip_compare,
                    )
                else:
                    build_package_from_github_msg(
                        build_dir=tmp,
                        dest_dir=index_dir_path,
                        repository=package.repository,
                        version=package.version,
                        sub_dir=path,
                        release_version=package.release_version,
                        requires=package.requires,
                        unrequires=package.unrequires,
                        compare=not package.skip_compare,
                    )
            if not no_index:
                generate_package_index(index_dir_path, package.name, origin)
        if not no_index:
            generate_index(index_dir_path)
    finally:
        shutil.rmtree(tmp)


@cli.command(help="generate message package")
@click.option(
    "-s",
    "--seach",
    "search_dir",
    type=click.Path(exists=True),
    help="message search root path",
)
@click.argument("path", type=click.Path(exists=True), required=True)
def genmsg(path: str, search_dir: str) -> None:
    package_dir = pathlib.Path(path)
    search_dir = pathlib.Path(search_dir) if search_dir is not None else None
    generate_rosmsg_from_action(package_dir / "msg", package_dir / "action")
    generate_package_from_rosmsg(
        package_dir, package_dir.name, None, search_dir
    )


if __name__ == "__main__":
    cli()
