#================================================================
# File:           playback.py
# Version:        0.3.0
# Last Updated:   2025-09-18
# Last Edited By: Nick Scilingo
# Description:    MPV-based video playback for idle loop + one-shot clips.
#================================================================
# /home/nscilingo/santa_mailbox/playback.py

import subprocess
import os
import signal
import time


class Player:
    """
    Simple MPV-based video player wrapper.

    - video_dir: root directory for all clips
      (e.g. /home/nscilingo/santa_mailbox/videos)

    All clip names passed in (idle, letter clips, donation clips) are treated
    as *relative* to video_dir. They can include subfolders, for example:

      idle_name      = "idle/idle_720p.mp4"
      letter_clips   = ["letters/letter1.mp4", "letters/letter2.mp4"]
      donation_clips = ["donations/donation1.mp4"]

    The main app is responsible for choosing which clip to play.
    """

    def __init__(self, video_dir: str) -> None:
        self.video_dir = video_dir
        self.idle_proc: subprocess.Popen | None = None

    def _clip_path(self, name: str) -> str:
        """
        Build a filesystem path for a given clip name.

        If 'name' is already absolute, it is returned as-is. Otherwise it is
        joined to video_dir, so foldered names like "letters/clip.mp4" work.
        """
        if os.path.isabs(name):
            return name
        return os.path.join(self.video_dir, name)

    # ------------------------------------------------------------------
    # Idle loop handling
    # ------------------------------------------------------------------

    def start_idle(self, idle_name: str = "idle.mp4") -> None:
        """
        Start the idle loop video if it is not already running.

        The idle video loops indefinitely in fullscreen, with no OSD.
        """
        # If an idle process already exists and is running, do nothing
        if self.idle_proc and self.idle_proc.poll() is None:
            return

        path = self._clip_path(idle_name)
        self.idle_proc = subprocess.Popen(
            [
                "mpv",
                "--fs",            # fullscreen
                "--no-osd-bar",    # no progress bar
                "--loop=inf",      # loop forever
                "--really-quiet",  # suppress most output
                path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop_idle(self) -> None:
        """
        Stop the idle loop video if it is running.
        """
        proc = self.idle_proc
        if not proc:
            return

        # If it already exited, just clear the reference
        if proc.poll() is not None:
            self.idle_proc = None
            return

        try:
            # Ask MPV nicely to stop
            proc.send_signal(signal.SIGINT)
            time.sleep(0.5)
            # If it did not exit, kill it hard
            if proc.poll() is None:
                proc.kill()
        finally:
            self.idle_proc = None

    # ------------------------------------------------------------------
    # One-shot clip playback
    # ------------------------------------------------------------------

    def play_once(self, clip_name: str, timeout: int = 65) -> None:
        """
        Play a single clip once in fullscreen, then exit.

        - clip_name: relative to video_dir (can include subfolders)
        - timeout: maximum seconds to wait before forcibly stopping MPV
        """
        path = self._clip_path(clip_name)
        proc = subprocess.Popen(
            [
                "mpv",
                "--fs",            # fullscreen
                "--no-osd-bar",    # no progress bar
                "--ontop",         # keep above other windows
                "--really-quiet",  # suppress most output
                path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Gracefully interrupt, then kill if needed
            proc.send_signal(signal.SIGINT)
            time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
