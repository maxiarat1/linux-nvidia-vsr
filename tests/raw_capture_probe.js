async () => {
  const v = document.querySelector("video");
  const rows = [];
  for (let i = 0; i < 12; i++) {
    const frame = new VideoFrame(v, { timestamp: 0 });
    const data = new Uint8Array(frame.allocationSize());
    const t = performance.now();
    const layout = await frame.copyTo(data);
    rows.push({
      format: frame.format,
      width: frame.codedWidth,
      height: frame.codedHeight,
      visible: frame.visibleRect.toJSON(),
      color: frame.colorSpace.toJSON(),
      bytes: data.length,
      copy_ms: performance.now() - t,
      layout,
    });
    frame.close();
  }
  return rows;
};
