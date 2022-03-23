import argparse
from argparse import Namespace

import subprocess
import os
import shutil
from git.repo import Repo
from datetime import datetime
import time
from typing import Generic, TypeVar

# When you want to write Rust but you use python
T = TypeVar('T')
class Ok(Generic[T]):
    def __init__(self, value: T) -> None:
        super().__init__()
        self.value = value
    def is_ok(self) -> bool:
        return True
    def is_err(self) -> bool:
        return False 
    def unwrap(self) -> T:
        return self.value
    def get_or_none(self) -> T | None:
        return self.value

class Err(Generic[T]):
    def __init__(self, reason: str) -> None:
        self.reason =  reason
    def is_ok(self) -> bool:
        return False 
    def is_err(self) -> bool:
        return True
    def unwrap(self) -> T:
        raise Exception("Tried to unwrap an Err, which had the following erro: " + self.reason)
    def get_or_none(self) -> T | None:
        return None

Result = Ok[T] | Err[T]


def parse_args() -> Namespace:
    parser = argparse.ArgumentParser(
        description="Given a reference to a flatpak, try to reproduce it and"
        "compare to the one from the repo."
    )
    parser.add_argument('remote', help="The name of the remote repository, i.e. flathub")
    parser.add_argument('flatpak_name', help="The name of the flatpak to reproduce")
    parser.add_argument(
        '-i',
        '--installation',
        help="Specifies the local installation to use, by default it will use the global flatpak install."
        ,default="default"
    )
    parser.add_argument(
        '-int','--interactive',
        help="If set, flatpaks install and build command will run in interactive mode, asking you for input.",
        action='store_true'
    )
    parser.add_argument('-c', '--commit',help="Commit number of the package to rebuild.")

    return parser.parse_args()

def flatpak_info(installation: str, package: str) -> Result[dict[str, str]]:
    result = subprocess.run(["flatpak", "info", "installation=" + installation, package], capture_output=True) 
    if result.returncode == 0:
        output = result.stdout.decode('UTF-8')
        return Ok(cmd_output_to_dict(output))
    return Err(result.stderr.decode('UTF-8'))


def cmd_output_to_dict(output: str) -> dict[str, str]:
    result = [map(str.strip, line.split(':', 1)) for line in output.split('\n') if ':' in line]
    resultDict: dict[str, str] = dict(result)
    return resultDict

def flatpak_install(remote: str, package: str, installation: str, interractive: bool) -> Result[None]:
    cmd = ["flatpak", "install", remote, package, "--installation=" + installation]
    if not interractive:
        cmd.append("--noninteractive")
    install = subprocess.run(cmd, stderr=subprocess.PIPE)
    match install.returncode:
        case 0:
            return Ok(None)
        case _:
            return Err(install.stderr.decode('UTF-8'))

def flatpak_date_to_datetime(date: str) -> datetime:
    split = date.split('+')
    time = split[0]
    time_zone = split[1]
    time_zone = '+' + time_zone[:2] + ':' + time_zone[2:]
    return datetime.fromisoformat(time + time_zone)

def installation_exists(name: str) -> bool:
    result = subprocess.run(["flatpak", "--installation="+name,"list"]) 
    return result.returncode == 0

def installation_path(name: str) -> Result[str]:
    flatpak_install_dir = "/etc/flatpak/installations.d/"
    install_confs = os.listdir(flatpak_install_dir)
    for config_file in install_confs:
        with open(flatpak_install_dir + config_file, mode='r') as file:
            content = file.readline().split('\n')
            header = content[0]
            attributes = [line.split('=', 1) for line in content[1:] if '=' in line]
            attributes = dict(attributes)
            if name in header:
                return Ok(attributes['Path'])

    return Err(f"Path of installation {name} was not found.")

def downgrade_package(package: str, commit: str, installation: str, interactive: bool) -> Result[None]:
    if not installation_exists(installation):
        return Err(installation + " is not a valid flatpak installation, please set it up manually.")

    # Require root privileges for security reasons
    cmd = ["sudo" + "flatpak", "update", package, "--installation="+installation, "--commit="+commit]
    if not interactive:
        cmd.append("--noninteractive")

    downgrade = subprocess.run(cmd, stderr=subprocess.PIPE) 

    if downgrade.returncode != 0:
       return Err(downgrade.stderr.decode('UTF-8')) 
    return Ok(None)

# TODO Get from url rather than remote name
def get_build_repo(remote: str, package: str) -> Result[str]:
    match remote:
        case "flathub":
            return Ok("https://github.com/flathub/"+package) 
        case _:
            return Err("Only flathub is supported for now.")

def rebuild(dir: str, remote: str, installation: str):
    manifests = [file for file in os.listdir() if file == "manifest.json"]
    if len(manifests) > 1:
        return Err("Multiple manifests found, it's ambiguous.")
    manifest = manifests[0]
    cmd = ["flatpak-builder","--install-deps-from=" + remote, "--disable-cache", "--force-clean", "--installation="+installation, "build", manifest]
    subprocess.run(cmd, cwd=dir)

def install_deps(dir: str, remote: str, installation: str):
    manifest = find_manifest(os.listdir())
    if manifest is None:
        return Err("Could not find manifest (none or too many of them are present)")
    cmd = ["flatpak-builder","--install-deps-from=" + remote, "--disable-cache", "--force-clean", "--installation="+installation, "build", manifest, "--install-deps-only"]
    subprocess.run(cmd, cwd=dir)

def find_manifest(files: list[str]) -> str | None:
    manifests = [file for file in files if file == "manifest.json"]
    if len(manifests) > 1:
        return None
    return manifests[0]

def main():
    args = parse_args()
    remote = args.remote
    package = args.flatpak_name
    installation = args.installation
    interactive = args.interactive
    commit = args.commit

    flatpak_install(remote, package, installation, interactive).unwrap()
    if commit:
        downgrade_package(package, commit, installation, interactive).unwrap()

    metadatas = flatpak_info(remote, package).unwrap()
    build_time = flatpak_date_to_datetime(metadatas['Date'])
    git_url = get_build_repo(remote, package).get_or_none()

    build_time_float = time.mktime(build_time.timetuple())

    # Init the build directory
    dir = package
    os.mkdir(dir)
    path = os.curdir + "/" + dir
    if git_url is not None:
        repo = Repo.clone_from(git_url,path)
        repo.submodule_update()

    install_path = installation_path(installation).unwrap()
    manifest_path = f"{install_path}/app/{package}/current/{commit if commit else 'current'}/files/manifest.json"

    shutil.copy(manifest_path, install_path)

    for root, _, files in os.walk(path):
        for file in files:
            # Try to only touch manifest files
            if file.endswith(".json") or file.endswith(".yml"):
                os.utime(os.path.join(root, file), (build_time_float, build_time_float))

    install_deps(path, remote, installation)
    rebuild(path, remote, installation)


if __name__ == '__main__':
    main()
