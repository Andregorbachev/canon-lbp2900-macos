# Canon LBP2900 on macOS: a captdriver-based driver

A print driver for the Canon LBP2900 / LBP2900B on current macOS (built and verified on
macOS 14.6.1 Sonoma, Apple Silicon). It is the `rastertocapt` CUPS filter from the
open-source [captdriver](https://github.com/mounaiban/captdriver) project (GPL-3.0) plus
a small macOS patch and a PPD file. No kext, no SIP changes, no daemons: the filter talks
to the printer through the stock CUPS USB backend.

**Status: it prints.** Installed on 2026-09-04 on macOS 14.6.1 (Apple Silicon) and
printing on a real LBP2900 (device id `MFG:Canon;MDL:LBP2900;CMD:CAPT;VER:2.1`):
single- and multi-page jobs, margins match the screen. Out-of-paper handling works on
the real printer too (2026-09-04, through CUPS): one automatic retry, then the button
LED comes on and printing resumes when the button is pressed. Photos and screenshots
print correctly since 0.2.3 (confirmed from an Intel Mac on macOS 12.6). Before any live
test the filter was run against an LBP2900 emulator (`tools/capt-fake-printer.py`): the Hi-SCoA
stream decoded back to the original page byte for byte.

## Why not a driver from scratch

- Canon never shipped an LBP2900 driver for macOS. The Canon `CUPSCAPT` package
  installed on this Mac (10.0.8, and the current 10.0.9) supports the LBP3000 and newer.
- CAPT is a closed protocol. It was reverse-engineered between 2004 and 2013 (Boichat,
  Galakhov, Bolsee); captdriver is the result: about 2300 lines of C, with the commands
  and the Hi-SCoA compression described in `vendor/captdriver/SPECS`. Writing it again
  would mean repeating years of USB sniffing, and some protocol bytes are still `?`.
- captdriver compiles on macOS with a single `cc` command (the original agalakhov tree
  without any change, the mounaiban fork after removing one line in `std.h`): the CUPS
  headers ship with the Xcode SDK and Apple's USB backend supports the side-channel and
  back-channel calls the filter needs.

## What is in this folder

| Path | What it is |
|---|---|
| `vendor/captdriver/` | captdriver sources, a copy of the commit named in `UPSTREAM.txt`. Do not edit. |
| `patches/lbp2900-macos.patch` | First patch on top of vendor: the LBP2900 status strategy, 100 ms polling, bounded waits, `PAGE:`/`STATE:` lines for the macOS queue (from [duy12i1i7/canon-LBP2900-for-macOS](https://github.com/duy12i1i7/canon-LBP2900-for-macOS), GPL-3.0), plus removal of `#define _POSIX_C_SOURCE` from `std.h`, without which the fork does not build on macOS. |
| `patches/lbp2900-paper-out-recovery.patch` | Second patch. Halftoning: the PPD asks for 8-bit grey and the filter dithers it (8x8 Bayer) with a page-size budget for the printer's 2 MB. Paper-out: detect a dropped page (no-paper flags, or the printer releasing the job by itself), release and re-acquire the printer, re-initialise it, re-send the page once, then blink the LED and wait for the button before every further attempt. Checks the printer's result codes and never sends page data while the printer is in an error state. Also logs a warning whenever the printer rejects a command. See "Known limitations". |
| `build.sh` | Build: copies vendor into `build/`, applies the patches from `patches/` in name order, compiles the filter and the test tools, compiles the PPD from `canon-lbp.drv` with `ppdc` and writes the absolute filter path into it. |
| `test.sh` | Verification without a printer, see below. Results in `build/test/`. |
| `install.sh`, `uninstall.sh` | Install and remove (need `sudo`). |
| `package.sh` | Builds `dist/canon-lbp2900-macos-<version>-universal.tar.gz` for installing on another Mac without Xcode. |
| `ppd/Canon-LBP2900-captdriver.ppd` | Generated PPD. Recreated by `build.sh`. |
| `tools/capt-fake-printer.py` | Printer emulator: stands in for the three CUPS backend channels (stdout, fd 3, fd 4) and answers CAPT commands per `SPECS` and `prn_lbp2900.c`, including the out-of-paper behaviour observed on the real unit. |
| `tools/compare-raster.py`, `tools/pbm-to-png.py`, `tools/make-photo-page.py` | Compare the decoded page with the source raster (halftoning 8-bit pages the way the filter does); render a PNG; generate the photo-like test page. |
| `tools/hiscoa-size.c`, `tools/halftone-size.c` | Measure the Hi-SCoA size of a 1-bit page, and of an 8-bit page under different dither matrices (built into `build/tools/`). |
| `tools/capt-probe.py` | Direct USB probe over libusb/pyusb (`.venv`, `brew install libusb`, run with `sudo`): printer status, USB resets, and a step-by-step out-of-paper experiment that logs every printer reply. Needed because through CUPS each attempt costs a printer power cycle. |
| `docs/research-notes.md` | What was checked and found: Canon's driver line-up, captdriver ports, the CAPT status record, the out-of-paper state machine. |
| `build/` | Everything generated. Deleted and recreated by `build.sh`. |

## Install from the release archive (no Xcode needed)

Works on any Mac with macOS 10.13 or newer, Intel or Apple Silicon.

1. Download `canon-lbp2900-macos-<version>-universal.tar.gz` from
   https://github.com/Andregorbachev/canon-lbp2900-macos/releases
2. Connect the LBP2900 over USB and switch it on.
3. In Terminal:

   ```bash
   cd ~/Downloads
   tar -xzf canon-lbp2900-macos-*-universal.tar.gz
   cd canon-lbp2900-macos-*-universal
   sudo ./install.sh
   ```

   The script puts the filter into `/Library/Printers/captdriver/`, the PPD into the
   system PPD folder, and creates the `Canon_LBP2900` queue. The `lpadmin: Printer
   drivers are deprecated…` warning is normal; macOS prints it for every PPD.
4. Test print: `lpr -P Canon_LBP2900 build/test/test-page.txt`, or any document from an
   application, printer "Canon LBP2900".

If the printer is not found ("LBP2900 not found on USB"), check the cable and power and
run `sudo ./install.sh` again. To remove: `sudo ./uninstall.sh` from the same folder.

## Build, test, install

Requires the Xcode Command Line Tools (`xcode-select --install`). No autotools.

```bash
./build.sh
```

```bash
./test.sh
```

`test.sh` does four things: a Hi-SCoA round trip on a real page; runs
`build/rastertocapt` against the printer emulator through the full CAPT cycle
(IDENT → JOB_BEGIN → START/UPLOAD → SET_PARMS → PRINT_DATA → FIRE → JOB_END), plus
five out-of-paper scenarios (tray runs empty on page 2 of 3; the printer drops the page
silently; the printer never releases the job; both; the tray stays empty through the
automatic retry so the button path is exercised) and a ~4 MB photo page against an
emulated 1 MB printer buffer; decodes the captured stream and
compares it with the raster byte for byte; renders `build/test/test-page-decoded.png`.

Install, with the printer switched on and connected over USB:

```bash
sudo ./install.sh
```

The script puts the filter into `/Library/Printers/captdriver/rastertocapt` and the PPD
into `/Library/Printers/PPDs/Contents/Resources/`, finds the printer with `lpinfo -v`
and creates the `Canon_LBP2900` queue. If the printer is not found it installs only the
filter and the PPD; run the command again once the printer is connected, or pass the URI
explicitly: `sudo ./install.sh 'usb://Canon/LBP2900?serial=...'`.

`/Library/Printers/` is deliberate: that is where Brother and Canon put their drivers,
and it survives macOS updates. `/usr/libexec/cups/filter/` is wiped by updates.

Remove:

```bash
sudo ./uninstall.sh
```

Move to another Mac without Xcode, Apple Silicon or Intel:

```bash
./package.sh
```

This produces `dist/canon-lbp2900-macos-<version>-universal.tar.gz`; on the other Mac
unpack it and run `sudo ./install.sh` inside the unpacked folder. The filter is a
universal binary (arm64 + x86_64) built with a minimum macOS version of 10.13. The Intel
slice was checked under Rosetta against the emulator: page data and parameters match the
arm64 build byte for byte, only the timestamps in the JOB_SETUP commands differ.

## First print: what to check

1. Turn on CUPS debugging and watch the log while printing:
   ```bash
   sudo cupsctl LogLevel=debug; tail -f /var/log/cups/error_log | grep CAPT
   ```
   Turn it off with `sudo cupsctl LogLevel=warn`.
2. Print a page with a border around the edge. Apple's `cgpdftoraster` rasterises only
   the imageable area (4722×6780 px for A4 with 5 mm margins), not the whole sheet as
   Ghostscript does on Linux. The filter centres each line horizontally and sends 6780
   lines with a zero top margin. If the print is shifted up by about 5 mm, the place to
   adjust is `paper.c` (`margin_height`) or `HWmargins` in `canon-lbp.drv`.
3. Check a multi-page document and the out-of-paper case. The LBP2900 does not resume by
   itself: it drops the unprinted page. The driver re-acquires the printer, re-initialises
   it and re-sends the page once (paper may already be there). If that attempt fails
   too, the button LED comes on and the macOS queue shows "out of paper": load paper and
   press the button, the page finishes. The page counters in the log (`pages a/b/c/d`)
   count from printer power-on, not per job; re-initialisation resets them to zero.
4. If a job stays "printing" after being cancelled: `cancel -a Canon_LBP2900`, then
   switch the printer off for 10 s. The printer's page counters get out of step after an
   interrupted job; this is known captdriver behaviour.

## Known limitations

- A pause after every page. The driver waits for the sheet to come out completely before
  sending the next page (that is how captdriver works; pages are not pipelined). Speed is
  below the rated 12 ppm, but page counters and out-of-paper handling stay unambiguous.
- Out of paper. What a real LBP2900 does (probe `tools/capt-probe.py`, 2026-09-04): it
  drops the fired page, sets UNINIT2 (and NOPAPER if its sensor saw the empty tray; after
  the last sheet in the cassette it may drop the page silently, without flags, after
  30 s), and sooner or later releases the job on its own (STATUS0 bit 0). From then on
  every command except status polls answers `88`; ReserveUnit answers `87` while the job
  is still held and `90` for the byte-`02` variant the Windows driver uses. Page data
  sent in that state hangs the printer until a power cycle: neither the printer-class
  SOFT_RESET nor a USB port reset helps. The working recovery: plain ReserveUnit →
  JOB_SETUP → START_1/2/3 → UPLOAD_2 (clears UNINIT, resets the counters) → the same
  page again. The no-paper flags clear after the reset even with an empty tray, and the
  printer tries to feed on every FIRE, so the driver makes one automatic retry and then
  blinks the LED (the button is only reported while the LED blinks), goes offline and
  waits for the button. Three earlier builds failed: (1) reset inside the job without
  checking result codes: hang; (2) passive waiting: the printer never resumes; (3) a new
  job without releasing the old one / with byte `02`: `87`/`90`. The emulator reproduces
  every variant; `./test.sh` runs six scenarios. Confirmed through CUPS on the real
  printer on 2026-09-04.
- Photos and other dense pages (fixed in 0.2.3, confirmed on a real LBP2900 from an Intel Mac, macOS 12.6). The
  LBP2900 has 2 MB of memory, not expandable, and a page has to fit into it as Hi-SCoA
  data. Hi-SCoA was designed for the regular screens of Canon's own driver; Apple's
  `cgpdftoraster` halftones with error diffusion, which does not compress at all: a
  screenshot page came out at 3.7 MB, printed as a collage of misplaced strips, was
  dropped by the printer and then hung it. Since 0.2.3 the PPD asks for 8-bit grey and
  the filter halftones the page itself with an 8x8 ordered dither (64 levels): the same
  page is about 0.7 MB. If a page still exceeds 1.25 MB it is halftoned again from 2x2,
  4x4 and 8x8 pixel averages until it fits; a page that would still exceed 1.5 MB is
  refused with an error instead of hanging the printer. Text is unaffected: thresholding
  8-bit grey gives the same bits as before (`./test.sh` compares them). The filter also
  paces page data by the printer's BUFFERFULL flag (STATUS0 bit 2). Tools:
  `build/tools/hiscoa-size page.pbm` and `build/tools/halftone-size image.pgm MATRIX`
  measure how much data a page becomes with different halftones.
- The paper-size code in the printer command is always A4: upstream takes the size name
  from the raster's `MediaType` field instead of `cupsPageSizeName`. The image size is
  right, so for A4 it makes no difference; Letter may show artefacts.
- Print density: an open upstream issue.
- The "N of M" progress in the macOS queue window does not move for non-AirPrint
  printers; that is a macOS limitation, not the driver's. The exact count is visible in
  `lpstat` and the log.
- Paper jams and an open cover are not recognised (those status bits are not decoded).

## How it works

The CUPS chain: application → PDF → `cgpdftoraster` → `rastertocapt` → `usb` backend →
printer. `rastertocapt` cuts the page into 70-line bands, compresses them with Hi-SCoA
(SPECS, section 3), and holds a two-way conversation with the printer: status requests go
through the CUPS back channel (fd 3) and side channel (fd 4), so the filter needs no
privileges and no USB stack of its own.

The first patch changes only the status-polling strategy: upstream reads the extended
status (command `0xA0A8`, which carries the page counters) only when the `XSTATUS_CHNG`
flag is set, and on the LBP2900 that flag, according to both ports, never appears, so the
end-of-page wait hangs. The patch reads the extended status always, as for the LBP3010.

## Provenance

- captdriver: `vendor/captdriver/UPSTREAM.txt` (mounaiban fork, commit 6271924,
  2022-10-14). Original: https://github.com/agalakhov/captdriver
- Patch: https://github.com/duy12i1i7/canon-LBP2900-for-macOS, `patches/lbp2900-macos.patch`,
  commit 12953b0 (2026-07-07). On top of it the patch removes
  `#define _POSIX_C_SOURCE 199309L` from `src/std.h`: the line exists in the mounaiban
  fork (not in agalakhov's original), on macOS it hides the `u_char`/`u_int` types in the
  system headers and the build fails; the port's author worked around it with
  `-D_DARWIN_C_SOURCE`, here the line is simply removed.
- The same port offers a precompiled binary, a "self-healing" LaunchDaemon and a menu-bar
  app. Here everything is built from source on this Mac and the filter lives where
  updates do not touch it, so no daemon is needed.
- The side-channel wire format (big-endian length) was checked against
  `cups/sidechannel.c` of CUPS 2.3.3.
- The CAPT protocol and the status record: `vendor/captdriver/SPECS`.
- The out-of-paper sequence: a USB capture of the Canon Windows driver on an LBP2900
  (HighwayStar, linux.org.ru, 2013) and live experiments with `tools/capt-probe.py`,
  see `docs/research-notes.md`.
