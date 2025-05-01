<!-- PROJECT INTRO -->

OrpheusDL - Beatsource
=================

A Beatsource module for the OrpheusDL modular archival music program

[Report Bug](https://github.com/bascurtiz/orpheusdl-beatsource/issues)
·
[Request Feature](https://github.com/bascurtiz/orpheusdl-beatsource/issues)


## Table of content

- [About OrpheusDL - Beatsource](#about-orpheusdl-beatsource)
- [Getting Started](#getting-started)
    - [Prerequisites](#prerequisites)
    - [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
    - [Global](#global)
    - [Beatsource](#Beatsource)
- [Contact](#contact)



<!-- ABOUT ORPHEUS -->
## About OrpheusDL - Beatsource

OrpheusDL - Beatsource is a module written in Python which allows archiving from **Beatsource** for the modular music archival program.


<!-- GETTING STARTED -->
## Getting Started

Follow these steps to get a local copy of Orpheus up and running:

### Prerequisites

* Already have [OrpheusDL](https://github.com/yarrm80s/orpheusdl) installed

### Installation

1. Go to your `orpheusdl/` directory and run the following command:
   ```sh
   git clone https://github.com/bascurtiz/orpheusdl-beatsource.git modules/beatsource
   ```
2. Execute:
   ```sh
   python orpheus.py
   ```
3. Now the `config/settings.json` file should be updated with the Beatsource settings

<!-- USAGE EXAMPLES -->
## Usage

Just call `orpheus.py` with any link you want to archive:

```sh
python orpheus.py https://www.beatsource.com/track/sweet-caroline/11575544
```

<!-- CONFIGURATION -->
## Configuration

You can customize every module from Orpheus individually and also set general/global settings which are active in every
loaded module. You'll find the configuration file here: `config/settings.json`

### Global

```json5
"global": {
    "general": {
        // ...
        "download_quality": "high"
    },
    "covers": {
        "main_resolution": 1400,
        // ...
    },
    // ...
}
```

`download_quality`: Choose one of the following settings:
* "hifi": same as lossless (only Beatsource Streaming Professional)
* "lossless": FLAC 16-bit 44.1kHz (only Beatsource Streaming Professional)
* "high": AAC 256 kbit/s (only Beatsource Streaming Professional)
* "medium": same as low
* "low": same as minimum
* "minimum": AAC 128 kbit/s

`main_resolution`: Beatsource supports resolutions from 100x100px to 1400x1400px max.
A value greater than `1400` is clamped at `1400` so that the cover is not scaled up.

### Beatsource
```json
{
    "username": "",
    "password": ""
}
```

| Option   | Info                                            |
|----------|-------------------------------------------------|
| username | Enter your Beatsource email/username address here |
| password | Enter your Beatsource password here               |

**NOTE: You need an active "Link" subscription to use this module. "Professional", formerly known as "LINK Pro" is
required to get  AAC 256 kbit/s.**

<!-- Contact -->
## Contact

Project Link: [OrpheusDL Beatsource Public GitHub Repository](https://github.com/bascurtiz/orpheusdl-beatsource)
