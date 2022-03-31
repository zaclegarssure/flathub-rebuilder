import argparse
from argparse import Namespace

import subprocess
import os
import re
import shutil
from git.repo import Repo
from datetime import datetime
import json
from typing import Generic, TypeVar

REMOTES_URLS_TO_LOCAL_DEPS = {
    "https://dl.flathub.org/repo/": "https://github.com/flathub/"
}

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
    parser.add_argument('-t', '--time', help="Time to use for the rebuild.")

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
    format = '%Y-%m-%d %H:%M:%S %z'
    return datetime.strptime(date, format)

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


def get_additional_deps(remote: str, installation: str, package: str) -> Result[str]:
    remote_url = flatpak_remote_url(remote, installation)
    if remote_url.is_ok:
        if remote_url.unwrap() in REMOTES_URLS_TO_LOCAL_DEPS:
            git_url = REMOTES_URLS_TO_LOCAL_DEPS[remote_url.unwrap()]
            return Ok(git_url + package)
        else:
            return Err("Unknown remote.")
    return remote_url

def rebuild(dir: str, installation: str, install: bool = False) -> Result[None]:
    manifest = find_manifest(os.listdir(dir))
    if manifest is None:
        return Err("Could not find manifest (none or too many of them are present)")
    cmd = ["flatpak-builder", "--disable-cache", "--force-clean", "--installation="+installation, "build", manifest, "--repo=repo"]
    if install:
        cmd.insert(0, "sudo")
        cmd.append("--install")
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

def flatpak_package_path(installation: str, package: str, arch: str | None = None) -> Result[str]:
    cmd = ["flatpak", "info", "-l", "--installation=" + installation, package]
    if (arch):
        cmd.append("--arch=" + arch)
    flatpak_info = subprocess.run(cmd, capture_output=True)
    if flatpak_info.returncode != 0:
       return Err(rebuild.stderr.decode('UTF-8')) 
    return Ok(flatpak_info.stdout.decode('UTF-8').strip())

def flatpak_remote_url(remote: str, installation: str) -> Result[str]:
    cmd = ["flatpak", "remotes", "--installation=" + installation, "--columns=name,url"]
    flatpak_remotes = subprocess.run(cmd, capture_output=True)
    if flatpak_remotes.returncode != 0:
       return Err(flatpak_remotes.stderr.decode('UTF-8')) 
    output = flatpak_remotes.stdout.decode('UTF-8').strip().split('\n')
    if (len(output) >= 1):
        remote_url = [line.split()[1] for line in output if line.split()[0] == remote]
        if (len(remote_url) == 1):
            return Ok(remote_url[0])
    return Err("Remote url not found.")

def find_time_in_binary(path: str) -> list[datetime]:
    cmd = ["strings", path]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        return []
    output = result.stdout.decode('UTF-8').splitlines()
    fulldate_pattern = re.compile(r"\w{3} \d\d \d{4} \d\d:\d\d:\d\d")
    dates: list[datetime] = []
    for str in output:
        match = fulldate_pattern.search(str)
        if match:
            date = match.group()
            date = datetime.strptime(date, "%b %d %Y %H:%M:%S")
            dates.append(date)

    return dates

def find_closest_time(flatpak_package_path: str, estimate: datetime) -> datetime:
    times: list[datetime] = []
    for root, _, files in os.walk(flatpak_package_path + "/files/"):
       for file in files:
          times.extend(find_time_in_binary(os.path.join(root, file)))

    if len(times) > 0:
        return times[min(range(len(times)), key=lambda t: abs(estimate.timestamp() - times[t].timestamp()))]

    return estimate


def main():
    args = parse_args()
    remote = args.remote
    package = args.flatpak_name
    installation = args.installation
    interactive = args.interactive
    commit = args.commit
    time = args.time

    git_url = get_additional_deps(remote, installation, package).get_or_none()

    if not installation_exists(installation):
        exit(1)

    flatpak_install(remote, package, installation, interactive).unwrap()
    if commit:
        pin_package_version(package, commit, installation, interactive).unwrap()
    else:
        flatpak_update(package, installation, interactive).unwrap()

    metadatas = flatpak_info(installation, package).unwrap()

    original_path = flatpak_package_path(installation, package).unwrap()

    # Init the build directory
    dir = package
    os.mkdir(dir)
    path = os.curdir + "/" + dir
    if git_url is not None:
        repo = Repo.clone_from(git_url,path)
        repo.submodule_update()

    if time:
        build_time = flatpak_date_to_datetime(time)
    else:
        build_time_estimate = flatpak_date_to_datetime(metadatas['Date'])
        build_time = find_closest_time(original_path, build_time_estimate)
    build_timestamp = build_time.timestamp()

    #install_path = installation_path(installation).unwrap()
    manifest_path = original_path + "/files/manifest.json"
    with open(manifest_path, mode='r') as manifest:
        manifest_content = manifest.read()
        manifest = parse_manifest(manifest_content).unwrap()

    shutil.copy(manifest_path, path)

    # Change time of manifests files
    for root, _, files in os.walk(path):
        for file in files:
            # Try to only touch manifest files
            if file.endswith(".json") or file.endswith(".yml"):
                os.utime(os.path.join(root, file), (build_timestamp, build_timestamp))

    #for sdk_extension in manifest['sdk-extensions']
    #    flatpak_install()

    install_deps(path, remote, installation)
    pin_package_version(manifest['sdk']+"/x86_64/"+manifest['runtime-version'], manifest['sdk-commit'], installation, interactive).unwrap()
    # A bit overkill but that ensures the everything is the same
    pin_package_version(manifest['runtime']+"/x86_64/"+manifest['runtime-version'], manifest['runtime-commit'], installation, interactive).unwrap()
    rebuild(path, installation, True).unwrap()


if __name__ == '__main__':
    main()
