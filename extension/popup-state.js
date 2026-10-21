// An enabled session is not evidence that enhanced pixels are on screen.
globalThis.VsrPopupState = (s) => {
  if (!s.video)
    return { kind: "empty", title: "No video selected", tag: "OFF" };
  if (!s.active && !s.starting)
    return { kind: "off", title: "Enhancement off", tag: "OFF" };
  if (s.bypass)
    return { kind: "original", title: "Original playback", tag: "ORIGINAL" };
  if (s.comparing)
    return { kind: "compare", title: "Comparing original", tag: "ORIGINAL" };
  if (s.paused)
    return { kind: "paused", title: "Enabled · video paused", tag: "PAUSED" };
  if (s.tabVisible === false)
    return { kind: "hidden", title: "Return to the video tab", tag: "WAITING" };
  if (s.presenting && !s.seeking)
    return { kind: "live", title: "Enhancement on", tag: "LIVE" };
  if (s.starting)
    return { kind: "starting", title: "Starting enhancement", tag: "STARTING" };
  return {
    kind: "waiting",
    title: "Waiting for enhanced frames",
    tag: "WAITING",
  };
};
