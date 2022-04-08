import argparse
from argparse import Namespace

import subprocess
import os
import re
import shutil
from git.repo import Repo
from datetime import datetime
from datetime import timezone
import json

REMOTES_URLS_TO_LOCAL_DEPS = {
    "https://dl.flathub.org/repo/": "https://github.com/flathub/"
}

def run_flatpak_command(cmd: list[str], installation: str, may_need_root = False, capture_output = False, cwd: str | None = None) -> str:
    if may_need_root and installation != "user":
        cmd.insert(0, "sudo")

    match installation:
        case "user":
            cmd.append("--user")
        case "system":
            cmd.append("--system")
        case _:
            cmd.append("--installation="+installation)

    if capture_output:
        result = subprocess.run(cmd, capture_output=True, cwd=cwd)
    else:
        result = subprocess.run(cmd, stderr=subprocess.PIPE, cwd=cwd)

    if result.returncode != 0:
        raise Exception(result.stderr.decode('UTF-8')) 
    else:
        if capture_output:
            return result.stdout.decode('UTF-8')
        else:
            return ""


def parse_args() -> Namespace:
    parser = argparse.ArgumentParser(
        description="Given a reference to a flatpak, try to reproduce it and"
        "compare to the one from the repo."
    )
    parser.add_argument('remote', help="The name of the remote repository, i.e. flathub")
    parser.add_argument('flatpak_name', help="The name of the flatpak to reproduce")
    parser.add_argument(
        '-int','--interactive',
        help="If set, flatpaks install and build command will run in interactive mode, asking you for input.",
        action='store_true'
    )
    parser.add_argument('-c', '--commit',help="Commit number of the package to rebuild.")
    parser.add_argument('-t', '--time', help="Time to use for the rebuild.")
    install_group = parser.add_mutually_exclusive_group()
    install_group.add_argument(
        '-i',
        '--installation',
        help="Specifies the local installation to use, by default it will use the user flatpak install."
    )
    install_group.add_argument(
        '--user',
        help="If sets, use user install.",
        action='store_true'
    )
    install_group.add_argument(
        '--system',
        help="If sets, use system install.",
        action='store_true'
    )

    return parser.parse_args()

def flatpak_info(installation: str, package: str) -> dict[str, str]:
    cmd = ["flatpak", "info", package]
    output = run_flatpak_command(cmd, installation, capture_output=True)
    return cmd_output_to_dict(output)

def cmd_output_to_dict(output: str) -> dict[str, str]:
    result = [map(str.strip, line.split(':', 1)) for line in output.split('\n') if ':' in line]
    resultDict: dict[str, str] = dict(result)
    return resultDict

def flatpak_install(remote: str, package: str, installation: str, interractive: bool):
    cmd = ["flatpak", "install", remote, package]
    if not interractive:
        cmd.append("--noninteractive")
    run_flatpak_command(cmd, installation)

def flatpak_date_to_datetime(date: str) -> datetime:
    format = '%Y-%m-%d %H:%M:%S %z'
    return datetime.strptime(date, format)

def installation_exists(name: str) -> bool:
    result = subprocess.run(["flatpak", "--installation="+name, "list"], capture_output=True) 
    return result.returncode == 0

def installation_path(name: str) -> str:
    if name == "user":
        return os.path.expanduser('~') + "/.local/share/flatpak/"
    elif name == "system":
        return "/var/lib/flatpak/"

    flatpak_install_dir = "/etc/flatpak/installations.d/"
    install_confs = os.listdir(flatpak_install_dir)
    for config_file in install_confs:
        with open(flatpak_install_dir + config_file, mode='r') as file:
            content = file.read().split('\n')
            header = content[0]
            attributes = [line.split('=', 1) for line in content[1:] if '=' in line]
            attributes = dict(attributes)
            if name in header:
                return attributes['Path']

    raise Exception(f"Path of installation {name} was not found.")

def pin_package_version(package: str, commit: str, installation: str, interactive: bool):
    cmd = ["flatpak", "update", package, "--commit="+commit]
    if not interactive:
        cmd.append("--noninteractive")

    run_flatpak_command(cmd, installation, may_need_root=True)

def flatpak_update(package: str, installation: str, interactive: bool):
    cmd = ["flatpak", "update", package]
    if not interactive:
        cmd.append("--noninteractive")

    run_flatpak_command(cmd, installation, may_need_root=True)

def get_additional_deps(remote: str, installation: str, package: str) -> str | None:
    remote_url = flatpak_remote_url(remote, installation)
    if remote_url in REMOTES_URLS_TO_LOCAL_DEPS:
        git_url = REMOTES_URLS_TO_LOCAL_DEPS[remote_url]
        return git_url + package
    else:
        return None

def find_flatpak_builder_commit_for_date(remote: str, installation: str, date: datetime) -> str:
    cmd = ["flatpak", "remote-info", remote, "org.flatpak.Builder", "--log"]
    output = run_flatpak_command(cmd, installation, capture_output=True)
    commits = output.split("\n\n")[1:]
    # We use the fact that --log return commits from the most recent to the oldest
    for commit in commits:
        commit = cmd_output_to_dict(commit)
        commit_date = flatpak_date_to_datetime(commit['Date'])
        if commit_date <= date:
            return commit['Commit']

    raise Exception("No commit matching the date has been found.")

def rebuild(dir: str, installation: str, package: str, branch: str, install: bool = False):
    manifest = find_build_manifest(os.listdir(dir), package)
    if manifest is None:
        manifest = find_manifest(os.listdir(dir))
        if manifest is None:
            raise Exception("Could not find manifest (none or too many of them are present)")

    cmd = ["flatpak", "run", "org.flatpak.Builder", "--disable-cache", "--force-clean", "build", manifest, "--repo=repo", "--bundle-sources", "--mirror-screenshots-url=https://dl.flathub.org/repo/screenshots", "--sandbox", "--default-branch=" + branch]
    if install:
        cmd.append("--install")

    run_flatpak_command(cmd, installation, may_need_root=install, cwd=dir)

def install_deps(dir: str, remote: str, installation: str):
    manifest = find_manifest(os.listdir(dir))
    if manifest is None:
        raise Exception("Could not find manifest (none or too many of them are present)")
    cmd = ["flatpak-builder","--install-deps-from=" + remote, "--disable-cache", "--force-clean", "build", manifest, "--install-deps-only"]
    run_flatpak_command(cmd, installation, cwd=dir)

def find_manifest(files: list[str]) -> str | None:
    manifests = [file for file in files if file == "manifest.json"]
    if len(manifests) > 1:
        return None
    return manifests[0]

# Hope I could avoid that
def find_build_manifest(files: list[str], package: str) -> str | None:
    manifests = [file for file in files if file == package + ".json" or file == package + ".yml" or file == package + ".yaml"]
    if len(manifests) > 1:
        return None
    return manifests[0]

def parse_manifest(manifest_content: str) -> dict[str, str]:
    return json.loads(manifest_content)

def flatpak_package_path(installation: str, package: str, arch: str | None = None) -> str:
    cmd = ["flatpak", "info", "-l", package]
    if (arch):
        cmd.append("--arch=" + arch)
    flatpak_info = run_flatpak_command(cmd, installation, capture_output=True)
    return flatpak_info.strip()

def flatpak_remote_url(remote: str, installation: str) -> str:
    cmd = ["flatpak", "remotes", "--columns=name,url"]
    flatpak_remotes = run_flatpak_command(cmd, installation, capture_output=True)
    if flatpak_remotes.isspace():
        raise Exception(f"No remotes in installation {installation}.")
    output = flatpak_remotes.strip().split('\n')
    if (len(output) >= 1):
        remote_url = [line.split()[1] for line in output if line.split()[0] == remote]
        if (len(remote_url) == 1):
            return remote_url[0]

    raise Exception("Remote not found.")

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
            date = date.replace(tzinfo=timezone.utc)
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
    user_install = args.user
    system_install = args.system
    custom_installation = args.installation
    interactive = args.interactive
    commit = args.commit
    time = args.time

    if user_install:
        installation = "user"
    elif system_install:
        installation = "system"
    elif custom_installation:
        installation = custom_installation
        if not installation_exists(installation):
            raise Exception(f"Installation {installation} does not exist.")
    else:
        installation = "user"

    git_url = get_additional_deps(remote, installation, package)

    flatpak_install(remote, package, installation, interactive)

    if commit:
        pin_package_version(package, commit, installation, interactive)
    else:
        flatpak_update(package, installation, interactive)

    metadatas = flatpak_info(installation, package)

    original_path = flatpak_package_path(installation, package)


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

    builder_commit = find_flatpak_builder_commit_for_date(remote, installation, build_time)

    #install_path = installation_path(installation).unwrap()
    manifest_path = original_path + "/files/manifest.json"
    with open(manifest_path, mode='r') as manifest:
        manifest_content = manifest.read()
        manifest = parse_manifest(manifest_content)

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
    pin_package_version(manifest['sdk']+"/x86_64/"+manifest['runtime-version'], manifest['sdk-commit'], installation, interactive)
    # A bit overkill but that ensures the everything is the same
    pin_package_version(manifest['runtime']+"/x86_64/"+manifest['runtime-version'], manifest['runtime-commit'], installation, interactive)
    pin_package_version("org.flatpak.Builder", builder_commit, installation, interactive)
    rebuild(path, installation, package, metadatas['Branch'], install=True)

if __name__ == '__main__':
    main()
