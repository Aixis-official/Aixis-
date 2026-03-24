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
      .catch((err) => sendResponse({ error: err.message }));
    return true; // async
  }
});

async function cropImage({ imageBase64, rect, devicePixelRatio }) {
  const dpr = devicePixelRatio || 1;

  // Scale rect by devicePixelRatio to match actual image pixels
  const sx = Math.round(rect.x * dpr);
  const sy = Math.round(rect.y * dpr);
  const sw = Math.round(rect.w * dpr);
  const sh = Math.round(rect.h * dpr);

  // Load the full image
  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = () => reject(new Error("Failed to load image for cropping"));
    img.src = `data:image/png;base64,${imageBase64}`;
  });

  // Set canvas to cropped dimensions
  const canvas = document.getElementById("canvas");
  canvas.width = sw;
  canvas.height = sh;

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, sw, sh);
  ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);

  // Export as base64 PNG (without data URL prefix)
  const dataUrl = canvas.toDataURL("image/png");
  const croppedBase64 = dataUrl.replace(/^data:image\/png;base64,/, "");

  return { croppedBase64 };
}
