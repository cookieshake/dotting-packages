function renderText(ctx) {
  ctx.canvas.text(
    0,
    0,
    ctx.inputs.value,
    ctx.config.color || '#ffffff',
    ctx.config.size || 16,
    ctx.resources.font(ctx.config.font || 'tom-thumb-4x6')
  );
}

function renderNumber(ctx) {
  ctx.canvas.text(
    0,
    0,
    String(ctx.inputs.value),
    ctx.config.color || '#ffffff',
    ctx.config.size || 16,
    ctx.resources.font(ctx.config.font || 'tom-thumb-4x6')
  );
}

function renderClock(ctx) {
  const date = new Date(ctx.time);
  const value = `${String(date.getUTCHours()).padStart(2, '0')}:${String(date.getUTCMinutes()).padStart(2, '0')}`;
  ctx.canvas.text(
    0,
    0,
    value,
    ctx.config.color || '#ffffff',
    16,
    ctx.resources.font(ctx.config.font || 'tom-thumb-4x6')
  );
}
