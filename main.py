#!/usr/bin/env python3
"""
Pirate Audio Headphone Jack HAT — Pygame Music Player
======================================================
Hardware: Pimoroni Pirate Audio Headphone Jack HAT (Raspberry Pi)

Button GPIO pins (BCM numbering, active LOW / internal pull-up):
  A  → GPIO  5  |  Play / Pause
  B  → GPIO  6  |  Next track
  X  → GPIO 16  |  Previous track
  Y  → GPIO 24  |  Cycle volume (30 → 50 → 70 → 100 %)

ST7789 240×240 display wiring:
  SPI port 0, CS = CE1 (cs=1), DC = GPIO 9, Backlight = GPIO 13

Dependencies (install with pip):
  pip install pygame pillow st7789 RPi.GPIO

Music directory:  ~/Music  (MP3 / WAV / OGG / FLAC, searched recursively)
Run:  python3 pirate_player.py [/path/to/music]
"""

import os
import sys
import glob
import time
import textwrap

import pygame
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont

# ── Try importing st7789; gracefully degrade to headless mode ──────────────
try:
    import st7789
    HAS_DISPLAY = True
except ImportError:
    HAS_DISPLAY = False
    print("[INFO] st7789 not found — running in headless mode (console only).")

# ── Hardware constants ──────────────────────────────────────────────────────
BTN_A = 5    # Play / Pause
BTN_B = 6    # Next
BTN_X = 16   # Previous
BTN_Y = 24   # Cycle volume
ALL_BUTTONS = (BTN_A, BTN_B, BTN_X, BTN_Y)

DISP_W   = 240
DISP_H   = 240
SPI_PORT = 0
SPI_CS   = 1     # CE1
DISP_DC  = 9
DISP_BL  = 13
SPI_HZ   = 80_000_000

# ── Palette ─────────────────────────────────────────────────────────────────
CLR_BG      = (12,  12,  22)
CLR_HEADER  = (25,  25,  50)
CLR_FOOTER  = (18,  18,  32)
CLR_WHITE   = (240, 240, 240)
CLR_GRAY    = (110, 110, 130)
CLR_ACCENT  = (255, 165,   0)   # orange
CLR_GREEN   = ( 50, 210, 120)
CLR_PAUSED  = (180, 180, 180)
CLR_BAR_BG  = ( 40,  40,  55)

# ── Supported audio extensions ───────────────────────────────────────────────
AUDIO_EXTS = ("*.mp3", "*.wav", "*.ogg", "*.flac")


# ─────────────────────────────────────────────────────────────────────────────
class Display:
    """Wraps the ST7789 display and Pillow rendering."""

    def __init__(self):
        self.active = False
        if not HAS_DISPLAY:
            return
        try:
            self._dev = st7789.ST7789(
                rotation=90,
                port=SPI_PORT,
                cs=SPI_CS,
                dc=DISP_DC,
                backlight=DISP_BL,
                spi_speed_hz=SPI_HZ,
            )
            self.active = True
        except Exception as exc:
            print(f"[WARN] Display init failed: {exc}")

        # Fonts — fall back to PIL default if DejaVu not present
        fpath = "/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf"
        try:
            self.f_lg  = ImageFont.truetype(fpath.format("-Bold"), 22)
            self.f_md  = ImageFont.truetype(fpath.format("")      , 17)
            self.f_sm  = ImageFont.truetype(fpath.format("")      , 13)
        except OSError:
            default = ImageFont.load_default()
            self.f_lg = self.f_md = self.f_sm = default

    def render(self, state: dict):
        """Draw the player UI and push it to the display."""
        img  = Image.new("RGB", (DISP_W, DISP_H), CLR_BG)
        draw = ImageDraw.Draw(img)

        # ── Header bar ────────────────────────────────────────────────────
        draw.rectangle([(0, 0), (DISP_W, 38)], fill=CLR_HEADER)
        draw.text((10, 9), "\u266a PIRATE AUDIO", font=self.f_md, fill=CLR_ACCENT)

        # ── Track name (wrapped, max 3 lines) ────────────────────────────
        name  = state.get("track_name", "—")
        lines = textwrap.wrap(name, width=22)[:3]
        y = 48
        for line in lines:
            draw.text((10, y), line, font=self.f_md, fill=CLR_WHITE)
            y += 22

        # ── Track counter ─────────────────────────────────────────────────
        idx   = state.get("index", 0)
        total = state.get("total", 0)
        counter_str = f"Track {idx + 1} / {total}" if total else "No tracks"
        draw.text((10, 122), counter_str, font=self.f_sm, fill=CLR_GRAY)

        # ── Playback status ───────────────────────────────────────────────
        playing = state.get("playing", False)
        if playing:
            status_txt = "\u25b6  PLAYING"
            status_clr = CLR_GREEN
        else:
            status_txt = "\u23f8  PAUSED"
            status_clr = CLR_PAUSED
        draw.text((10, 145), status_txt, font=self.f_lg, fill=status_clr)

        # ── Volume label + bar ────────────────────────────────────────────
        vol = state.get("volume", 0.7)
        draw.text((10, 175), f"VOL  {int(vol * 100):3d}%", font=self.f_sm, fill=CLR_GRAY)
        bar_x0, bar_y0 = 10, 191
        bar_x1, bar_y1 = DISP_W - 10, 203
        draw.rectangle([(bar_x0, bar_y0), (bar_x1, bar_y1)], fill=CLR_BAR_BG)
        fill_x1 = bar_x0 + int((bar_x1 - bar_x0) * vol)
        draw.rectangle([(bar_x0, bar_y0), (fill_x1, bar_y1)], fill=CLR_ACCENT)

        # ── Footer hints ──────────────────────────────────────────────────
        draw.rectangle([(0, 210), (DISP_W, DISP_H)], fill=CLR_FOOTER)
        draw.text(( 5, 212), "[A] Play/Pause    [Y] Vol", font=self.f_sm, fill=CLR_GRAY)
        draw.text(( 5, 226), "[X] Prev          [B] Next", font=self.f_sm, fill=CLR_GRAY)

        if self.active:
            self._dev.display(img)

    def print_state(self, state: dict):
        """Minimal console output for headless / debug mode."""
        status = "▶ PLAYING" if state["playing"] else "⏸ PAUSED"
        print(
            f"\r{status}  [{state['index']+1}/{state['total']}] "
            f"{state['track_name'][:50]:<50}  VOL {int(state['volume']*100)}%",
            end="",
            flush=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
class MusicPlayer:
    VOLUMES = (0.30, 0.50, 0.70, 1.00)   # cycle targets

    def __init__(self, music_dir: str):
        self.music_dir = os.path.expanduser(music_dir)
        self.tracks: list[str] = []
        self.index  = 0
        self.playing = False
        self._vol_idx = 2          # default → 70 %
        self.volume  = self.VOLUMES[self._vol_idx]
        self._loaded = False

        self._scan_tracks()
        self._init_audio()
        self._init_gpio()
        self.display = Display()

    # ── Setup ────────────────────────────────────────────────────────────────

    def _scan_tracks(self):
        for ext in AUDIO_EXTS:
            self.tracks.extend(
                glob.glob(os.path.join(self.music_dir, "**", ext), recursive=True)
            )
        self.tracks.sort()
        print(f"[INFO] Found {len(self.tracks)} track(s) in {self.music_dir}")

    def _init_audio(self):
        pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
        pygame.mixer.init()
        pygame.mixer.music.set_volume(self.volume)

    def _init_gpio(self):
        # Clear any stale edge-detection state left by a previous (crashed) run.
        # Without this, RPi.GPIO raises "Failed to add edge detection" on restart.
        GPIO.cleanup()

        GPIO.setmode(GPIO.BCM)
        for pin in ALL_BUTTONS:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        callbacks = {
            BTN_A: self._cb_play_pause,
            BTN_B: self._cb_next,
            BTN_X: self._cb_prev,
            BTN_Y: self._cb_volume,
        }
        for pin, cb in callbacks.items():
            # Defensive remove — safe to call even if no detection exists yet.
            try:
                GPIO.remove_event_detect(pin)
            except Exception:
                pass
            GPIO.add_event_detect(pin, GPIO.FALLING, callback=cb, bouncetime=300)

    # ── Button callbacks (called from GPIO interrupt thread) ─────────────────

    def _cb_play_pause(self, _channel):
        if not self.tracks:
            return
        if self.playing:
            pygame.mixer.music.pause()
            self.playing = False
        else:
            if self._loaded and pygame.mixer.music.get_pos() >= 0:
                pygame.mixer.music.unpause()
            else:
                self._load_and_play()
            self.playing = True

    def _cb_next(self, _channel):
        self.index = (self.index + 1) % max(len(self.tracks), 1)
        self._load_and_play(force=self.playing)

    def _cb_prev(self, _channel):
        self.index = (self.index - 1) % max(len(self.tracks), 1)
        self._load_and_play(force=self.playing)

    def _cb_volume(self, _channel):
        self._vol_idx = (self._vol_idx + 1) % len(self.VOLUMES)
        self.volume = self.VOLUMES[self._vol_idx]
        pygame.mixer.music.set_volume(self.volume)

    # ── Audio helpers ────────────────────────────────────────────────────────

    def _load_and_play(self, force: bool = True):
        if not self.tracks:
            return
        try:
            pygame.mixer.music.load(self.tracks[self.index])
            self._loaded = True
            if force:
                pygame.mixer.music.play()
                self.playing = True
        except Exception as exc:
            print(f"\n[ERROR] Cannot load track: {exc}")

    def _auto_advance(self):
        """If a track finished, move to the next one."""
        if self.playing and not pygame.mixer.music.get_busy():
            self.index = (self.index + 1) % max(len(self.tracks), 1)
            self._load_and_play(force=True)

    # ── State ────────────────────────────────────────────────────────────────

    def _state(self) -> dict:
        if self.tracks:
            name = os.path.splitext(os.path.basename(self.tracks[self.index]))[0]
        else:
            name = "No tracks found"
        return {
            "track_name": name,
            "index"     : self.index,
            "total"     : len(self.tracks),
            "playing"   : self.playing,
            "volume"    : self.volume,
        }

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        if self.tracks:
            self._load_and_play(force=False)
        try:
            while True:
                self._auto_advance()
                state = self._state()
                self.display.render(state)
                if not HAS_DISPLAY or not self.display.active:
                    self.display.print_state(state)
                time.sleep(0.25)
        except KeyboardInterrupt:
            print("\n[INFO] Stopped by user.")
        finally:
            pygame.mixer.quit()
            GPIO.cleanup()


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    music_path = sys.argv[1] if len(sys.argv) > 1 else "~/Music"
    player = MusicPlayer(music_path)
    player.run()
