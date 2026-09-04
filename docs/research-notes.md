# Research notes (September 2026)

What was established before building, and how well it is verified. The facts were
gathered by agents with web access; the "Canon" and "captdriver" summaries were
re-checked in a second pass against the primary sources (58 of 67 claims confirmed
verbatim, discrepancies only in details such as the number of branches in a repository).
The "alternatives" and "protocol" summaries did not get a second pass because of session
limits; they are marked below as unverified.

## Canon (verified)

- Canon has never had an LBP2900/2900B driver for macOS. The model pages (Canon Asia,
  India, Europe, UK) offer only Windows (R1.50 Ver.3.30, 2015/2017) and Linux V2.71
  (2017-05-15); there is no macOS entry in the OS selector.
  https://asia.canon/en/support/LASER%20SHOT%20LBP2900__%202900B/model
- The current "CAPT Printer Driver & Utilities for Mac V10.0.9" (2026-08-28,
  macOS 10.13.6–26) supports the LBP3000, 3050, 3150, 3250, 3300, 3310, 3500,
  5000/5050/5100/5300, 6000/6018B/6200d/6300dn, 7018C/7200, 9100Cdn. No LBP2900.
  The older V3.90 and V3.93 lack it too. https://in.canon/en/support/0100999320
- Exactly that package is installed on this Mac: `jp.co.canon.CUPSCAPT.sub.pkg` 10.0.8,
  installed 2025-10-04; it contains no PPD for the LBP2900 (checked locally).
- The folk hack "install the LBP3000 driver and hex-patch `captmoncnab3` and
  `CnAC28B9.DAT`" worked up to Catalina/Big Sur; for Sonoma and Sequoia the Apple
  Community threads report only failures (14.1, 14.4.1, 14.5, 15.3.1).

## captdriver (verified)

- The original agalakhov/captdriver stopped at 175f8ff (2022-10-08, 0.1.3); its README
  calls the project "passively maintained". The mounaiban fork: master 6271924
  (2022-10-14, 0.1.4.1), issues/wiki still active (push 2026-08-13).
- The LBP2900 is registered as WORKS, tested on x86-64 and ARMv7 Linux. Known problems:
  the `0xE0A0` status poll looping on 32-bit systems (mounaiban #3), a "Rendering
  completed" hang cured by switching the printer off (agalakhov #7), reduced print
  density (mounaiban #33, open).
- The code does not depend on Linux: only the CUPS raster API, `cupsBackChannelRead`,
  `cupsSideChannelDoRequest` (DRAIN_OUTPUT, GET_DEVICE_ID). Apple's USB backend
  (`usb-darwin.c`) implements these; confirmed on this Mac from the strings in
  `/usr/libexec/cups/backend/usb`.
- Three independent macOS ports, all changing the same thing: the LBP2900 is switched to
  unconditional reading of the extended status (`capt_get_xstatus_only`).
  HardNorth/captdriver (2025-09-27, PR #47 in mounaiban closed without merging),
  duy12i1i7/canon-LBP2900-for-macOS (13 commits on 2026-07-07, the author tested on
  macOS 26 Apple Silicon; one third-party PR reports "works on M5"),
  bechou0410/canon-lbp2900-macos27-driver (June 2026, requires the Canon LBP3000 driver
  to be installed).
- Nobody has published a result specifically on macOS 14 Sonoma.

## Alternatives (no second pass)

- A Linux print server (Raspberry Pi) with captdriver, shared over IPP/Bonjour: the most
  predictable route, but captdriver on ARM has its own history of hangs. Canon's official
  Linux driver V2.71 is x86 only (i386/x86_64) with proprietary blobs; it does not run on
  a Pi.
- Virtual machines on Apple Silicon do not help: Canon's Windows driver is x86 only,
  printer drivers do not install in an ARM guest; x86 emulation is slow and has no
  confirmed reports.
- No commercial driver covers the LBP2900 (PrintFab does inkjets only, Gutenprint has no
  CAPT).
- OpenPrinting CUPS 2.4.17 (April 2026) fixed a DRAIN_OUTPUT race in the Linux libusb
  backend; it does not apply to macOS (IOKit backend).

## Protocol (no second pass)

- The LBP2900 is CAPT 2.1 with Hi-SCoA compression, which `SPECS` describes completely.
  The command layer is described less well: of the ~25 opcodes the driver uses, not all
  are worked out in SPECS; the STATUS5/6 bits and some page-parameter bytes are `?`.
- The reverse engineering was done by sniffing USB under Windows and running Canon's
  `captfilter` on known images: Boichat 2004 (LBP-810), Galakhov 2010–2013 (LBP-2900),
  Bolsee 2010 (LBP-3010). A separate CAPT v1 implementation (darkvision77/libcapt,
  2025–2026, LBP800–3200 only) took one developer about half a year.
- Conclusion: writing from scratch would mean repeating that path without new knowledge.

## Local checks on this Mac (2026-09-04)

- macOS 14.6.1, arm64, CUPS 2.3.4, Xcode 16; the `cups/raster.h`, `sidechannel.h`,
  `ppd.h` headers are in the SDK; `ppdc` and `cupsfilter` ship with the system.
- The original agalakhov tree builds with a single `cc -std=c99 -pedantic` without
  changes. The mounaiban fork (vendor) with the same flags fails because of
  `#define _POSIX_C_SOURCE 199309L` in `std.h` (it hides `u_char`/`u_int` in Apple's
  headers); the patch removes that line. The binary is ad-hoc signed by the linker, which
  is all that is needed to run.
- Hi-SCoA round trip on a real A4 page: 0 errors.
- Full run through the printer emulator (`tools/capt-fake-printer.py`): every CAPT stage
  passed, the page decoded back, 6780 lines matched byte for byte. The side-channel wire
  format (big-endian length) checked against `cups/sidechannel.c`.
- Apple's `cgpdftoraster` returns a raster of the imageable area (4722×6780 px for A4
  with 5 mm margins), not of the whole sheet; with the "old" PPD from the repository root
  (zero margins) text at the left edge was clipped, so the PPD is compiled from
  `canon-lbp.drv`.
- In that same old PPD toner saving is set through `cupsCompression` while the code reads
  `cupsInteger0`, which Apple's rasteriser sets to 1: toner save would always be on. With
  the PPD from the drv the value is correct (0/1 as selected).

## Out of paper on a real LBP2900 (2026-09-04)

- Job 73 (the build that restarted the job after the button): after FIRE of page 3 with
  an empty tray, STATUS0 = `8A12` (NOPAPER1 + UNINIT2), STATUS1 = `4084` → `4000`
  (NOPAPER2; PRINTING goes off after 3 s), the `page_out` counter does not advance. The
  button shows as STATUS1 bit 5 (`4020`) and STATUS2 `0080`. Loading paper does not clear
  the flags.
- After the button, a new job without releasing the old one: IDENT and START_0 answer
  normally, JOB_BEGIN (ReserveUnit) → `87 00 00 00`, every following command (E1A2,
  E1A1, E0A3, E0A2, E0A4, E0A5) → `88 00`, and the printer does not answer page data at
  all ("no reply from printer", then USB transaction timeouts). Conclusion: byte 0 of a
  reply is a result code, `00` = accepted; a printer in error rejects commands, and data
  in that state hangs it (as Galakhov wrote).
- USB capture of the Canon Windows driver on an LBP2900 with an empty tray (HighwayStar,
  paste.org.ru/?m77anq and linux.org.ru/forum/linux-hardware/4868236, September 2013):
  `E1A1 fg=06` → `E0A9` (ReleaseUnit) → ~8 s of `A0A8`/`A0A1` polling → `A2A0` with
  byte 0 = `02` → `E1A1 fg=02` → `E0A3`, `E0A2`, `E0A4` → `E0A5` (GoOnline) → `E1A2`
  with bytes `00 00 01 02 01 00 … 01 00` (identical to `lbp2900_gpio_blink`) →
  `E0A6 06 00 00 00` (GoOffline) → `E0A0` polling until the button → `E1A1` → `E0A3`,
  `E0A2`, `E0A4` … → `D0A9` + the same page's data. There is no separate FIRE without
  re-sending the page. The capture contains only OUT packets; the printer's replies are
  not in it.
- A hung printer (page data sent in the error state, job 75) cannot be revived from the
  host: bulk writes time out, the printer-class SOFT_RESET request (bRequest 2) is
  answered with a STALL, a USB port reset (re-enumeration) does not help
  (`tools/capt-probe.py usb-reset`, 2026-09-04). Only a power cycle. Rule for the driver:
  never send page data while the printer answers with rejection codes or holds
  NOPAPER/UNINIT.
- Probe `capt-probe.py paper-out`, run 3 (2026-09-04 09:42): the cassette empty after the
  last sheet. After FIRE the printer did not feed for 30 s (no PRINTING, no NOPAPER),
  then S0 `8A11`: bit 0 (job released) + UNINIT2, the printing counter did not move.
  Loading paper and pressing the button are not visible in the status without the LED
  blinking. In this state: GPIO/JOB_SETUP/START/UPLOAD/JOB_END → `88`; ReserveUnit `02`
  → `90`; ReserveUnit `00` → `00` (bit 0 cleared); JOB_SETUP fg=2, START_1/2/3, UPLOAD_2
  → `00`, UNINIT cleared, counters 0; the page was re-sent and came out. Summary: `87` =
  unit still held, `88` = command not allowed with no unit reserved, `90` = ReserveUnit
  refused. The patch gained the `CAPT_FL_NOJOB` flag (STATUS0 bit 0) and a
  `capt_last_result` check after every step.
- CUPS job 78 (the build with the verified sequence): with NOPAPER flagged, ReserveUnit
  `00` answered `90` for about 10 s (7–10 attempts one second apart) and then `00`: the
  `90` is the printer's own paper check after a drop, repeated every ~15 s while the tray
  stays empty. After UPLOAD_2 the NOPAPER flags cleared even though the tray was still
  empty, and the next FIRE found out again. Hence the final design: one automatic re-send,
  then blink, GoOffline and wait for the button before every further attempt. Switching
  the LED off on the PRINTING flag was wrong: the printer raises it for a moment on every
  feed attempt before it knows the tray is empty.
- The emulator `tools/capt-fake-printer.py` models all of this: reservation state and the
  `87`/`88`/`90` codes, the `90` window after a drop, the hang on data in an error state,
  NOPAPER cleared by UPLOAD_2, a silent drop, a printer that never releases the job, and
  a button that is only visible while the LED blinks. `./test.sh` runs six scenarios.

## Photos: Hi-SCoA versus Apple's halftone (2026-09-04)

- A screenshot printed from an Intel Mac (macOS 12.6) came out as a collage of strips;
  the job ended with every command rejected (`88`) and the printer hung. The raster
  header on that Mac is identical to Sonoma's (4722 x 6780, 1 bpp, 591 B/line), and the
  x86_64 slice produces a byte-identical CAPT stream under Rosetta, so neither the
  architecture nor the rasteriser geometry is the cause.
- The LBP2900 has 2 MB of memory (vendor specifications), not expandable. A synthetic
  photo page halftoned by `cgpdftoraster` (error diffusion, visibly random) is 3.7 MB of
  Hi-SCoA data; 45 of 97 bands are larger than raw. The same image with ordered dithers
  (`build/tools/halftone-size`): smooth photo 0.26–0.75 MB depending on the matrix,
  worst-case noise 1.5–2.2 MB; Floyd–Steinberg 4.6 MB. Block averaging before dithering
  brings the noisy page to 1.4 MB (2x2), 0.95 MB (4x4), 0.7 MB (8x8).
- Hence 0.2.3: `canon-lbp.drv` asks for 8-bit grey (`Resolution k 8 …`), `rastertocapt`
  dithers with an 8x8 Bayer matrix, re-dithers from 2x2/4x4/8x8 averages when the page
  exceeds 1.25 MB and refuses pages above 1.5 MB. The exact safe limit of the printer is
  not known; 1.5 MB leaves a quarter of its memory for the firmware.
- Sources: Canon LBP2900 specifications ([BlueArm](https://bluearm.ph/products/canon-lbp-2900-laser-printer),
  [VillMan](https://villman.com/Product-Detail/LBP2900)).
