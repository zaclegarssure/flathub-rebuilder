
<h1 align="center">
  <br>
  <img src="https://github.com/zaclegarssure/flathub-rebuilder/blob/main/Component%2013.png" alt="Flathub-rebuilder" width="300">
  <br>
  Flatpak Rebuilder
  <br>
</h1>

<h4 align="center">A CLI tool to verify a flatpak locally by using <a href="https://reproducible-builds.org/">reproducible builds</a>.</h4>

<p align="center">
  <a href="#key-features">Key Features</a> •
  <a href="#installation">Installation</a> •
  <a href="#usage">Usage</a>
</p>


## Key Features

* Recreate a flatpak from flathub just by it's name.
* Support custom flatpak installation, to avoid breaking your main install.
* Pipe the result to diffoscope and capture a few statistics, useful for analysis.

## Installation

This program is built using the [poetry](https://github.com/python-poetry/poetry) python dependency management tool.
Once installed you simply clone the repo and run the following:
```bash

# Install dependencies
$ poetry install

# Run
$ poetry run flatpak-rebuilder <remote> <package>
```

You can also use `poetry shell` to spawn a shell in the local install and run the commands without `poetry run`.

If you want to use a custom installation, here is how you can create one.
Make sure this directory exist:
```bash
$ sudo mkdir -p /etc/flatpak/installations.d
```
Each custom installation has it's own config file ending in `.conf` in this directory.
You need to create one with root privileges, for instance with the following content:
```bash
$ cat /etc/flatpak/installations.d/rebuilder.conf
[Installation "rebuilder"]
Path=/home/<username>/flatpak-rebuilder-install/
DisplayName=Flatpak rebuilder installation
StorageType=harddisk
```
The above installation will be located in `~/flatpak-rebuilder-install/`. 
See the [flatpak documentation](https://docs.flatpak.org/en/latest/flatpak-command-reference.html#flatpak-installation)
or their [tips and tricks](https://docs.flatpak.org/en/latest/tips-and-tricks.html) page to learn more.
One issue is that custom installations are system wide, meaning that you will be asked for root permission while running the script.
By default it will use the user installation, which does not require any other privilege.

The script will setup the flathub and flathub-beta remotes for you, if not already set up.

## Usage
It works in the same way `flatpak install` does, namely by providing the name of the package to rebuild.
The following options are valid:
* `--commit=COMMIT` The commit number of the package to rebuild, if you want to rebuild an older version.
* `--installation=INSTALLATION` The name of the flatpak installation to use.
* `--interactive` If set, will run the commands without the `--noninteractive` flag, which will ask you if you want to install the dependencies.

Here is an example:
```bash
$ flatpak-rebuilder org.gnome.Dictionary --installation=rebuilder --interactive
```
This will create the `org.gnome.Dictionary` directory, with `build` and `repo` sub-directories that contains the rebuild
and an ostree repo of the rebuild, the original and rebuild version of the programs are both checkout in the same directory, at `<package-name>.original` and `<package-name>.rebuild`.

Be aware that this requires root privileges at certain moment in order to downgrade packages, except if you use the user install.

## Security
Even though the build is run in a sandbox, it will still run basically arbitrary code, so make sure to check what's written in the manifest file of the program you are about to rebuild, to decide if it can be trusted or not. Or just run things in a Docker or a VM.
