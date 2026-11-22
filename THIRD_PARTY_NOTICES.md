# Third-party notices

Studio Multicam is under the [GNU General Public License v3](LICENSE). The material below is bundled with it or derived from others' work, and keeps its own license. The license texts are in `hub/hubd/static/shared/vendor/`, so the hub serves them along with the files they cover.

## Code

| Work | Where | License |
|---|---|---|
| [Preact](https://github.com/preactjs/preact) 10.19.3, Copyright (c) 2015-present Jason Miller | `hub/hubd/static/shared/vendor/preact.module.js`, `hooks.module.js` | [MIT](hub/hubd/static/shared/vendor/licenses/preact-MIT.txt) |
| [htm](https://github.com/developit/htm) 3.1.1, Copyright 2018 Google Inc. | `hub/hubd/static/shared/vendor/htm.module.js` | [Apache 2.0](hub/hubd/static/shared/vendor/licenses/htm-Apache-2.0.txt) |
| [uPlot](https://github.com/leeoniya/uPlot) 1.6.30, Leon Sorokin | `hub/hubd/static/uPlot.iife.min.js`, `uPlot.min.css` | [MIT](hub/hubd/static/shared/vendor/licenses/uplot-MIT.txt) |
| [mp4box.js](https://github.com/gpac/mp4box.js) 0.5.2, Telecom ParisTech and GPAC contributors | `hub/hubd/static/editor/vendor/`, `hub/spike/e2/vendor/` | [BSD 3-Clause](hub/hubd/static/shared/vendor/licenses/mp4box-BSD-3-Clause.txt) |
| [DataStream.js](https://github.com/kig/DataStream.js), Ilmari Heikkinen | Inside mp4box.js | [Apache 2.0](hub/hubd/static/shared/vendor/licenses/Apache-2.0.txt) |
| [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) 1.4.4, Copyright (c) 2009 Kazuhiko Arase | `hub/hubd/static/qrcode.js` (modified: its UTF-8 helper was replaced) | [MIT](hub/hubd/static/shared/vendor/licenses/qrcode-generator-MIT.txt) |

## Derived data

| Work | Where | License |
|---|---|---|
| [CSSgram](https://github.com/una/CSSgram), Copyright (c) 2015 Una Kravets | The classic filters' recipes in `hub/hubd/static/editor/looks.json`, applied by `grade.js` and `grade.py` (adapted) | [MIT](hub/hubd/static/shared/vendor/licenses/cssgram-MIT.txt) |
| [OpenCV](https://github.com/opencv/opencv) ArUco dictionary `DICT_4X4_50` | The marker patterns in `hub/calib/card.json` and the printed card | [Apache 2.0](hub/hubd/static/shared/vendor/licenses/Apache-2.0.txt), [copyright](hub/hubd/static/shared/vendor/licenses/opencv-COPYRIGHT.txt) |

## Fonts

All are under the SIL Open Font License 1.1, separately from the GPL. Files are in `hub/hubd/static/shared/vendor/fonts/`; each license file carries the copyright notice.

| Font | Form | License |
|---|---|---|
| Archivo Black | Fontsource Latin subset | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-archivo-black.txt) |
| Bungee | Fontsource Latin subset | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-bungee.txt) |
| DSEG7 and DSEG14 Classic | The author's files, unmodified (reserved name) | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-dseg.txt) |
| Elastic | A modified version of Barlow Regular | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-barlow-elastic.txt) |
| Figtree | Unmodified | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-figtree.txt) |
| Jost | Fontsource Latin subset | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-jost.txt) |
| Nixie One | Google Fonts' file, unmodified (reserved name "Nixie") | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-nixie-one.txt) |
| Orbitron | Google Fonts' file, unmodified (reserved name) | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-orbitron.txt) |
| Oswald | Fontsource Latin subset | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-oswald.txt) |
| Outfit | Unmodified | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-outfit.txt) |
| Righteous | Google Fonts' file, unmodified (reserved name) | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-righteous.txt) |
| Shrikhand | Fontsource Latin subset | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-shrikhand.txt) |
| VT323 | Fontsource Latin subset | [OFL](hub/hubd/static/shared/vendor/fonts/OFL-vt323.txt) |

Don't subset, convert or edit a font with a reserved name and keep that name: the OFL forbids it.

## Not bundled

The installer downloads MediaMTX (MIT) and Raspberry Pi OS packages (ffmpeg, OpenCV, MKVToolNix and others) from their own distributors. Film overlays and music are not part of the repository.

## Trademarks

Raspberry Pi, Android, Chrome, Pixel, YouTube, Instagram, Logic, OBS, Casio, Blue Note and other names are their owners' trademarks, used only to say what something works with or resembles. This project is not affiliated with or endorsed by any of them. The classic filters are approximations, not Instagram's own.
