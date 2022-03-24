import argparse
from argparse import Namespace

import subprocess
import os
import shutil
from git.repo import Repo
from datetime import datetime
import time
import json
from typing import Generic, TypeVar

# Not super useful since I unwrap every results, but I just wanted to play around with python's type annotations.
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
        raise Exception("Tried to unwrap an Err, which had the following error: " + self.reason)
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
    result = subprocess.run(["flatpak", "info", "--installation=" + installation, package], capture_output=True) 
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
    result = subprocess.run(["flatpak", "--installation="+name,"list"], capture_output=True) 
    return result.returncode == 0

def installation_path(name: str) -> Result[str]:
    flatpak_install_dir = "/etc/flatpak/installations.d/"
    install_confs = os.listdir(flatpak_install_dir)
    for config_file in install_confs:
        with open(flatpak_install_dir + config_file, mode='r') as file:
            content = file.read().split('\n')
            header = content[0]
            attributes = [line.split('=', 1) for line in content[1:] if '=' in line]
            attributes = dict(attributes)
            if name in header:
                return Ok(attributes['Path'])

    return Err(f"Path of installation {name} was not found.")

def pin_package_version(package: str, commit: str, installation: str, interactive: bool) -> Result[None]:
    # Require root privileges for security reasons
    cmd = ["sudo", "flatpak", "update", package, "--installation="+installation, "--commit="+commit]
    if not interactive:
        cmd.append("--noninteractive")

    # Could also be an upgrade
    downgrade = subprocess.run(cmd, stderr=subprocess.PIPE) 

    if downgrade.returncode != 0:
       return Err(downgrade.stderr.decode('UTF-8')) 
    return Ok(None)

def flatpak_update(package: str, installation: str, interactive: bool) -> Result[None]:
    cmd = ["flatpak", "update", package, "--installation="+installation]
    if not interactive:
        cmd.append("--noninteractive")

    update = subprocess.run(cmd, stderr=subprocess.PIPE) 

    if update.returncode != 0:
       return Err(update.stderr.decode('UTF-8')) 
    return Ok(None)


# TODO Get from url rather than remote name
def get_build_repo(remote: str, package: str) -> Result[str]:
    match remote:
        case "flathub":
            return Ok("https://github.com/flathub/"+package) 
        case _:
            return Err("Only flathub is supported for now.")

def rebuild(dir: str, installation: str) -> Result[None]:
    manifest = find_manifest(os.listdir(dir))
    if manifest is None:
        return Err("Could not find manifest (none or too many of them are present)")
    cmd = ["flatpak-builder", "--disable-cache", "--force-clean", "--installation="+installation, "build", manifest, "--repo=repo"]
    rebuild = subprocess.run(cmd, cwd=dir, stderr=subprocess.PIPE)
    if rebuild.returncode != 0:
       return Err(rebuild.stderr.decode('UTF-8')) 
    return Ok(None)

def install_deps(dir: str, remote: str, installation: str):
    manifest = find_manifest(os.listdir(dir))
    if manifest is None:
        return Err("Could not find manifest (none or too many of them are present)")
    cmd = ["flatpak-builder","--install-deps-from=" + remote, "--disable-cache", "--force-clean", "--installation="+installation, "build", manifest, "--install-deps-only"]
    subprocess.run(cmd, cwd=dir)

def find_manifest(files: list[str]) -> str | None:
    manifests = [file for file in files if file == "manifest.json"]
    if len(manifests) > 1:
        return None
    return manifests[0]

def parse_manifest(manifest_content: str) -> Result[dict[str, str]]:
    try:
        return Ok(json.loads(manifest_content))
    except:
        return Err("Can't parse manifest file.")

def main():
    args = parse_args()
    remote = args.remote
    package = args.flatpak_name
    installation = args.installation
    interactive = args.interactive
    commit = args.commit

    if not installation_exists(installation):
        exit(1)

    flatpak_install(remote, package, installation, interactive).unwrap()
    if commit:
        pin_package_version(package, commit, installation, interactive).unwrap()
    else:
        flatpak_update(package, installation, interactive).unwrap()

    metadatas = flatpak_info(installation, package).unwrap()
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
    manifest_path = f"{install_path}/app/{package}/current/{commit if commit else 'active'}/files/manifest.json"
    with open(manifest_path, mode='r') as manifest:
        manifest_content = manifest.read()
        manifest = parse_manifest(manifest_content).unwrap()

    shutil.copy(manifest_path, path)

    # Change time of manifests files
    for root, _, files in os.walk(path):
        for file in files:
            # Try to only touch manifest files
            if file.endswith(".json") or file.endswith(".yml"):
                os.utime(os.path.join(root, file), (build_time_float, build_time_float))

    #for sdk_extension in manifest['sdk-extensions']
    #    flatpak_install()

    install_deps(path, remote, installation)
    pin_package_version(manifest['sdk']+"/x86_64/"+manifest['runtime-version'], manifest['sdk-commit'], installation, interactive).unwrap()
    # A bit overkill but that ensures the manifests are the same
    pin_package_version(manifest['runtime']+"/x86_64/"+manifest['runtime-version'], manifest['runtime-commit'], installation, interactive).unwrap()
    rebuild(path, installation).unwrap()


if __name__ == '__main__':
    main()
