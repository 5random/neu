# CVD-Tracker

CVD-Tracker is a small Python application for monitoring a CVD reactor.

## Project Description

For further details, see [projektbeschreibung.md](projektbeschreibung.md).

## Installation

Install [Raspberry Pi OS Lite](https://www.raspberrypi.com/documentation/computers/getting-started.html) on your Raspberry Pi.

Clone the repository:

```bash
sudo apt install git
git clone https://collaborating.tuhh.de/cuf3111/cvd_tracker.git
cd neu
```

Run the setup script:

```bash
bash setup.sh full
```

Follow the prompts. The script installs the required system packages, micromamba, the Python environment, configures the Pi, writes the application settings, and enables the service. Enter IP-Adress for Proxy HTTPS.

## Usage

1. Connect the UVC webcam to the Raspberry Pi.
2. Power on the Pi and wait until the setup has finished.
3. Open the URL shown by the setup script.
