// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  ChevronLeft,
  Maximize,
  Minimize,
  Pause,
  Play,
  Volume2,
  VolumeX,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type CSSProperties,
} from "react";
import { useTranslation } from "react-i18next";
import styles from "./login.module.css";

interface WatchPlayerProps {
  src: string;
  poster?: string;
  title: string;
  onClose: () => void;
}

// Playback speeds cycled by the "1x" chip (real playbackRate, unlike the
// decorative quality label — the CDN serves a single MP4 with no ladder).
const PLAYBACK_SPEEDS = [1, 1.25, 1.5, 2] as const;
const CONTROLS_IDLE_MS = 2600;

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export function WatchPlayer({ src, poster, title, onClose }: WatchPlayerProps) {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const idleTimerRef = useRef<number | undefined>(undefined);

  const [playing, setPlaying] = useState(true);
  const [muted, setMuted] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [speedIndex, setSpeedIndex] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);

  const revealControls = useCallback(() => {
    setControlsVisible(true);
    window.clearTimeout(idleTimerRef.current);
    idleTimerRef.current = window.setTimeout(() => {
      // Only fade the chrome away while actually playing — a paused player
      // keeps its controls so the user can find play/seek.
      if (videoRef.current && !videoRef.current.paused) {
        setControlsVisible(false);
      }
    }, CONTROLS_IDLE_MS);
  }, []);

  useEffect(() => {
    revealControls();
    return () => window.clearTimeout(idleTimerRef.current);
  }, [revealControls]);

  // Esc closes the player — but only when we're NOT in native fullscreen,
  // where the browser reserves Esc to leave fullscreen first.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.fullscreenElement) {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    const onFullscreenChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play().catch(() => {});
    } else {
      video.pause();
    }
    revealControls();
  };

  const toggleMuted = () => {
    const next = !muted;
    setMuted(next);
    if (videoRef.current) videoRef.current.muted = next;
  };

  const cycleSpeed = () => {
    const next = (speedIndex + 1) % PLAYBACK_SPEEDS.length;
    setSpeedIndex(next);
    if (videoRef.current) videoRef.current.playbackRate = PLAYBACK_SPEEDS[next];
    revealControls();
  };

  const toggleFullscreen = () => {
    const root = rootRef.current;
    if (!root) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => {});
    } else {
      void root.requestFullscreen().catch(() => {});
    }
  };

  const handleSeek = (event: ChangeEvent<HTMLInputElement>) => {
    const value = Number(event.target.value);
    setCurrent(value);
    if (videoRef.current) videoRef.current.currentTime = value;
  };

  const progressPct = duration > 0 ? (current / duration) * 100 : 0;

  return (
    <div
      ref={rootRef}
      className={styles.playerOverlay}
      data-idle={controlsVisible ? undefined : "true"}
      onMouseMove={revealControls}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <video
        ref={videoRef}
        className={styles.playerVideo}
        src={src}
        poster={poster}
        autoPlay
        playsInline
        onClick={togglePlay}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onEnded={() => {
          setPlaying(false);
          setControlsVisible(true);
        }}
      />

      <div className={styles.playerTopBar}>
        <button type="button" className={styles.playerBack} onClick={onClose}>
          <ChevronLeft strokeWidth={1.9} aria-hidden="true" />
          <span>{t("common.back")}</span>
        </button>
      </div>

      <div className={styles.playerControls}>
        <button
          type="button"
          className={styles.playerPlay}
          onClick={togglePlay}
          aria-label={playing ? t("auth.watch.pause") : t("auth.watch.play")}
        >
          {playing ? (
            <Pause size={20} strokeWidth={2} aria-hidden="true" />
          ) : (
            <Play size={20} strokeWidth={2} aria-hidden="true" />
          )}
        </button>

        <span className={styles.playerTime}>
          {formatTime(current)} / {formatTime(duration)}
        </span>

        <input
          type="range"
          className={styles.playerSeek}
          min={0}
          max={duration || 0}
          step="any"
          value={current}
          onChange={handleSeek}
          aria-label={t("auth.watch.seek")}
          style={{ "--player-progress": `${progressPct}%` } as CSSProperties}
        />

        <button
          type="button"
          className={styles.playerSpeed}
          onClick={cycleSpeed}
          aria-label={t("auth.watch.speed")}
        >
          {PLAYBACK_SPEEDS[speedIndex]}x
        </button>

        <span className={styles.playerQuality}>{t("auth.watch.quality")}</span>

        <button
          type="button"
          className={styles.playerIconBtn}
          onClick={toggleMuted}
          aria-label={muted ? t("auth.watch.unmute") : t("auth.watch.mute")}
        >
          {muted ? (
            <VolumeX size={18} strokeWidth={1.9} aria-hidden="true" />
          ) : (
            <Volume2 size={18} strokeWidth={1.9} aria-hidden="true" />
          )}
        </button>

        <button
          type="button"
          className={styles.playerIconBtn}
          onClick={toggleFullscreen}
          aria-label={isFullscreen ? t("auth.watch.exitFullscreen") : t("auth.watch.fullscreen")}
        >
          {isFullscreen ? (
            <Minimize size={18} strokeWidth={1.9} aria-hidden="true" />
          ) : (
            <Maximize size={18} strokeWidth={1.9} aria-hidden="true" />
          )}
        </button>
      </div>
    </div>
  );
}
