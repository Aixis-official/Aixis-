/**
 * Aixis Chrome Extension v2 — Offscreen Document
 *
 * Receives CROP_IMAGE messages from the service worker.
 * Uses a canvas to crop a base64 PNG image to the given rect,
 * accounting for devicePixelRatio.
 */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "CROP_IMAGE" && message.target === "offscreen") {
    cropImage(message)
      .then(sendResponse)
      .catch((err) => sendResponse({ error: err.message || "Crop failed" }));
    return true; // async
  }
});

async function cropImage({ imageBase64, rect, devicePixelRatio }) {
  if (!imageBase64 || !rect) {
    throw new Error("Missing imageBase64 or rect for cropping");
  }

  const dpr = devicePixelRatio || 1;

  // Scale rect by devicePixelRatio to match actual image pixels
  const sx = Math.round(rect.x * dpr);
  const sy = Math.round(rect.y * dpr);
  const sw = Math.round(rect.w * dpr);
  const sh = Math.round(rect.h * dpr);

  // Validate dimensions
  if (sw <= 0 || sh <= 0) {
    throw new Error("Invalid crop dimensions: width or height is zero or negative");
  }

  // Load the full image with timeout
  const img = new Image();
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error("Image loading timed out (10s)"));
    }, 10000);

    img.onload = () => {
      clearTimeout(timeout);
      resolve();
    };
    img.onerror = () => {
      clearTimeout(timeout);
      reject(new Error("Failed to load image for cropping"));
    };
    img.src = `data:image/png;base64,${imageBase64}`;
  });

  // Clamp crop region to image bounds to prevent blank output
  const clampedSx = Math.max(0, Math.min(sx, img.naturalWidth));
  const clampedSy = Math.max(0, Math.min(sy, img.naturalHeight));
  const clampedSw = Math.min(sw, img.naturalWidth - clampedSx);
  const clampedSh = Math.min(sh, img.naturalHeight - clampedSy);

  if (clampedSw <= 0 || clampedSh <= 0) {
    throw new Error("Crop region is entirely outside the image bounds");
  }

  // Get or create canvas element
  let canvas = document.getElementById("canvas");
  if (!canvas) {
    canvas = document.createElement("canvas");
    canvas.id = "canvas";
    document.body.appendChild(canvas);
  }

  canvas.width = clampedSw;
  canvas.height = clampedSh;

  const ctx = canvas.getContext("2d");
  if (!ctx) {
    throw new Error("Failed to get 2D canvas context");
  }

  ctx.clearRect(0, 0, clampedSw, clampedSh);
  ctx.drawImage(img, clampedSx, clampedSy, clampedSw, clampedSh, 0, 0, clampedSw, clampedSh);

  // Export as base64 PNG (without data URL prefix)
  const dataUrl = canvas.toDataURL("image/png");
  const croppedBase64 = dataUrl.replace(/^data:image\/png;base64,/, "");

  return { croppedBase64 };
}
