import argparse
from argparse import Namespace

import subprocess
import os
import re
import shutil
from git.repo import Repo
from datetime import datetime
import time
from datetime import timezone
from pathlib import Path
import json

REMOTES_URLS_TO_LOCAL_DEPS = {
    "https://flathub.org/repo/": "https://github.com/flathub/"
}

FLATPAK_BUILDER = "org.flatpak.Builder"
APPSTREAM_GLIB = "org.freedesktop.appstream-glib"
FLAT_MANAGER = "org.flatpak.flat-manager-client"
EXTERNAL_DATA_CHECKER = "org.flathub.flatpak-external-data-checker"

def run_flatpak_command(cmd: list[str], installation: str, may_need_root = False, capture_output = False, cwd: str | None = None, interactive = True, arch: str | None = None) -> str:
    if may_need_root and installation != "user":
        cmd.insert(0, "sudo")

    match installation:
        case "user":
            cmd.append("--user")
        case "system":
            cmd.append("--system")
        case _:
            cmd.append("--installation="+installation)

    if arch:
        cmd.append("--arch="+arch)

    if not interactive:
        cmd.append("--noninteractive")

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
    parser.add_argument('flatpak_name', help="The name of the flatpak to reproduce")
    parser.add_argument(
        '-int','--interactive',
        help="If set, flatpaks install and build command will run in interactive mode, asking you for input.",
        action='store_true'
    )
    parser.add_argument('-c', '--commit',help="Commit number of the package to rebuild.")
    parser.add_argument('-t', '--time', help="Time to use for the rebuild.")
    parser.add_argument('-a', '--arch',help="Cpu architeture to use for the build, by default will use the one available on the system.")
    parser.add_argument('--estimate-time', help="Let flatpak rebuilder find a time estimate of the build by scraping binaries.", action='store_true')

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
    result = [list(map(str.strip, line.split(':', 1))) for line in output.split('\n') if ':' in line]
    resultDict: dict[str, str] = dict(result)
    return resultDict

def flatpak_install(remote: str, package: str, installation: str, interractive: bool, arch: str, or_update: bool = False):
    cmd = ["flatpak", "install", remote, package]
    if not interractive:
        cmd.append("--noninteractive")
    if or_update:
        cmd.append("--or-update")
    run_flatpak_command(cmd, installation, arch=arch)

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

def get_additional_deps(remote: str, package: str) -> str | None:
    if remote == "flathub":
        return "https://github.com/flathub/" + package
    elif remote == "flathub-beta":
        return None
    return None

def find_flatpak_commit_for_date(remote: str, installation: str, package: str, date: datetime) -> str:
    cmd = ["flatpak", "remote-info", remote, package, "--log"]
    output = run_flatpak_command(cmd, installation, capture_output=True)
    commits = output.split("\n\n")[1:]
    # We use the fact that --log return commits from the most recent to the oldest
    for commit in commits:
        commit = cmd_output_to_dict(commit)
        commit_date = flatpak_date_to_datetime(commit['Date'])
        if commit_date <= date:
            return commit['Commit']

    raise Exception("No commit matching the date has been found.")

def rebuild(dir: str, installation: str, package: str, branch: str, arch: str, install: bool = False) -> tuple[int, float]:
    manifest = find_build_manifest(os.listdir(dir), package)
    if manifest is None:
        manifest = find_manifest(os.listdir(dir))
        if manifest is None:
            raise Exception("Could not find manifest (none or too many of them are present)")

    extra_fb_args = ['--arch', arch]
    if arch == 'x86_64':
        extra_fb_args.append('--bundle-sources')

    cmd = ["flatpak", "run", "org.flatpak.Builder", "--disable-cache", "--force-clean", "build", manifest, "--download-only"]
    run_flatpak_command(cmd, installation, cwd=dir)

    download_size = sum(f.stat().st_size for f in Path(dir + '/.flatpak-builder').rglob('*'))

    cmd = ["flatpak", "run", "org.flatpak.Builder", "--disable-cache", "--force-clean", "build", manifest, "--repo=repo", "--mirror-screenshots-url=https://dl.flathub.org/repo/screenshots", "--sandbox", "--default-branch=" + branch, *extra_fb_args, '--remove-tag=upstream-maintained', "--disable-download"]
    if install:
        cmd.append("--install")

    before = time.process_time()
    run_flatpak_command(cmd, installation, may_need_root=install, cwd=dir)
    after = time.process_time()

    return (download_size, after - before)

def install_deps(dir: str, remote: str, installation: str, arch: str):
    manifest = find_manifest(os.listdir(dir))
    if manifest is None:
        raise Exception("Could not find manifest (none or too many of them are present)")
    cmd = ["flatpak", "run", "org.flatpak.Builder", "--install-deps-from=" + remote, "--disable-cache", "--force-clean", "build", manifest, "--install-deps-only"]
    run_flatpak_command(cmd, installation, cwd=dir, arch=arch)

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

def ostree_checkout(repo: str, ref: str, dest: str, root = False):
    cmd = ["ostree", "checkout", ref, dest, "--repo="+repo, "-U"]
    if root:
        cmd.insert(0, "sudo")

    subprocess.run(cmd).check_returncode()

def run_diffoscope(original_path: str, rebuild_path: str, html_output: str | None = None) -> int:
    cmd = ["diffoscope", original_path, rebuild_path, "--exclude-directory-metadata=yes"]
    if html_output:
        cmd.append("--html="+html_output)

    return subprocess.run(cmd).returncode

def flatpak_uninstall(package: str, installation: str, interactive: bool, arch: str):
    cmd = ["flatpak", "uninstall", package]
    run_flatpak_command(cmd, installation, interactive=interactive, arch=arch)

def get_default_arch() -> str:
    cmd = ["flatpak", "--default-arch"]

    result = subprocess.run(cmd, capture_output=True)
    result.check_returncode()

    return result.stdout.decode('UTF-8').strip()

def is_arch_available(arch: str) -> bool:
    cmd = ["flatpak", "--supported-arches"]
    result = subprocess.run(cmd, capture_output=True)
    result.check_returncode()

    available = result.stdout.decode('UTF-8').split('\n')
    return arch in available

def flatpak_remote_add(remote: str, installation: str, url: str, gpg_import: str | None = None):
    cmd = ["flatpak", "remote-add", "--if-not-exists", remote, url]
    if gpg_import:
        cmd.append("--gpg-import=" + gpg_import)

    run_flatpak_command(cmd, installation, may_need_root=True)

def flatpak_remote_modify_url(remote: str, installation: str, url: str):
    cmd = ["flatpak", "remote-modify", "--url="+url, remote]

    run_flatpak_command(cmd, installation, may_need_root=True)

def ostree_init(repo: str, mode: str, path: str):
    cmd = ["ostree", "--repo=" + repo, "--mode="+mode, "init"]
    subprocess.run(cmd, cwd=path).check_returncode()

def generate_deltas(repo_dir: str, repo: str):
    cmd = 'flatpak build-update-repo --generate-static-deltas --static-delta-ignore-ref=*.Debug --static-delta-ignore-ref=*.Sources ' + repo
    subprocess.run(cmd, cwd=repo_dir, shell=True).check_returncode()

def main():
    args = parse_args()
    remote = "flathub"
    package = args.flatpak_name
    user_install = args.user
    system_install = args.system
    custom_installation = args.installation
    interactive = args.interactive
    commit = args.commit
    time = args.time
    arch = args.arch


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

    if arch is None:
        arch = get_default_arch()
    elif not is_arch_available(arch):
        raise Exception(f"Cannot build, because {arch} is not an available architeture on your system.")

    # Should get the right commit in case of older build
    git_url = get_additional_deps(remote, package)


    # Add flathub and flathub-beta as remotes
    flatpak_remote_add("flathub", installation, "https://flathub.org/repo/flathub.flatpakrepo")
    # Make sure the name of the remote is flathub
    flatpak_remote_modify_url("flathub", installation, "https://flathub.org/repo/")
    # Same but with flathub beta
    flatpak_remote_add("flathub-beta", installation, "https://flathub.org/beta-repo/flathub-beta.flatpakrepo")
    flatpak_remote_modify_url("flathub-beta", installation, "https://flathub.org/beta-repo/flathub-beta.flatpakrepo")

    flatpak_install(remote, package, installation, interactive, arch)

    if commit:
        pin_package_version(package, commit, installation, interactive)
    else:
        flatpak_update(package, installation, interactive)

    metadatas = flatpak_info(installation, package)

    original_path = flatpak_package_path(installation, package)


    # Init the build directory
    dir = package
    os.mkdir(dir)
    path = f"{os.curdir}/{dir}"
    if git_url is not None:
        repo = Repo.clone_from(git_url,path)
        repo.submodule_update()

    if time:
        build_time = flatpak_date_to_datetime(time)
    elif args.estimate_time:
        build_time_estimate = flatpak_date_to_datetime(metadatas['Date'])
        build_time = find_closest_time(original_path, build_time_estimate)
    else:
        build_time = flatpak_date_to_datetime(metadatas['Date'])

    build_timestamp = build_time.timestamp()

    flatpak_install("flathub", "org.flatpak.Builder", installation, interactive, arch)
    builder_commit = find_flatpak_commit_for_date(remote, installation, FLATPAK_BUILDER, build_time)
    pin_package_version(FLATPAK_BUILDER, builder_commit, installation, interactive)

    manifest_path = f"{original_path}/files/manifest.json"
    with open(manifest_path, mode='r') as manifest:
        manifest_content = manifest.read()
        manifest = parse_manifest(manifest_content)

    flatpak_install(remote, f"{manifest['sdk']}/{arch}/{manifest['runtime-version']}", installation, interactive, arch)
    pin_package_version(f"{manifest['sdk']}/{arch}/{manifest['runtime-version']}", manifest['sdk-commit'], installation, interactive)

    flatpak_install(remote, f"{manifest['runtime']}/{arch}/{manifest['runtime-version']}", installation, interactive, arch)
    # A bit overkill but that ensures the everything is the same
    pin_package_version(f"{manifest['runtime']}/{arch}/{manifest['runtime-version']}", manifest['runtime-commit'], installation, interactive)

    #shutil.copy(manifest_path, path)
    ostree_init("repo", mode="archive-z2", path=path)

    # Change time of manifests files
    for root, _, files in os.walk(path):
        for file in files:
            # Try to only touch manifest files
            if file.endswith(".json") or file.endswith(".yml"):
                os.utime(os.path.join(root, file), (build_timestamp, build_timestamp))

    original_artifact = package+".original"
    rebuild_artifact = package+".rebuild"
    report = package+".report.html"

    for sdk_extension in manifest.get('sdk-extensions', []):
        full_name = f"{sdk_extension}/{arch}/{manifest['runtime-version']}"
        flatpak_install(remote, full_name, installation, interactive, arch)
        extenstion_commit = find_flatpak_commit_for_date(remote, installation, full_name, build_time)
        pin_package_version(full_name, extenstion_commit, installation, interactive)

    base_app = manifest.get('base')
    if base_app != None:
        full_name = f"{base_app}/{arch}/{manifest['base-version']}"
        flatpak_install(remote, full_name, installation, interactive, arch)
        base_app_commit = find_flatpak_commit_for_date(remote, installation, full_name, build_time)
        pin_package_version(full_name, base_app_commit, installation, interactive)

    install_path = installation_path(installation)
    ostree_checkout(f"{install_path}/repo", metadatas['Ref'], original_artifact, root=(installation != "user"))

    (dep_size, build_length) = rebuild(path, installation, package, metadatas['Branch'], arch, install=False)

    generate_deltas(path, "repo")

    ostree_checkout(f"{path}/repo", metadatas['Ref'], rebuild_artifact, root=(installation != "user"))

    # Clean up
    flatpak_uninstall(package, installation, interactive, arch)

    result = run_diffoscope(original_artifact, rebuild_artifact, report)

    # Make sure we only leave one directory
    shutil.move(original_artifact, f"{path}/{original_artifact}")
    shutil.move(rebuild_artifact, f"{path}/{rebuild_artifact}")

    # Keep a few stats to analyse later on.
    statistics = {
        "dep_size": dep_size,
        "build_length": build_length,
    }
    statistics = json.dumps(statistics, indent=4)
    with open(f"{path}/stats.json", "w") as f:
        f.write(statistics)

    # Report is only created when build is not reproducible
    if result != 0:
        shutil.move(report, f"{path}/{report}")

    exit(result)

if __name__ == '__main__':
    main()
