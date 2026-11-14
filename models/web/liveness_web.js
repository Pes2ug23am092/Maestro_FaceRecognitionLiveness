// Browser liveness inference (for the kiosk edge agent / offline mode). Same ONNX file as the Lambda.
//   <script src="https://cdn.jsdelivr.net/npm/onnxruntime-web@1.20.1/dist/ort.min.js"></script>
//   const live = await LivenessWeb.load('/models/liveness_cnn.onnx');
//   const crops = frames.map(f => LivenessWeb.contextCrop(f.canvas, f.box));   // box = {x,y,w,h} of the detected face
//   const r = await live.score(crops);   // {spoof_prob, attack_probs:{live,print,screen_photo,video_replay,mask}}
//
// Crop recipe MUST match Python (liveness/face_crop.py): square crop centred on the face box, 2.0x the larger side,
// edge-replicated, resized to 160x160. Face detection in the browser: use MediaPipe FaceDetector
// (@mediapipe/tasks-vision) or the Chrome FaceDetector API; pass its bounding box as {x,y,w,h}.
// Preprocess: RGB, /255, ImageNet mean/std, NCHW float32. Output: logits[5] in order live,print,screen_photo,video_replay,mask.
// Verify with models/web/parity_vectors.json (expected probabilities from onnxruntime in Python, tolerance 1e-3).
const LivenessWeb = (() => {
  const CLASSES = ['live', 'print', 'screen_photo', 'video_replay', 'mask'];
  const MEAN = [0.485, 0.456, 0.406], STD = [0.229, 0.224, 0.225], SIZE = 160, SCALE = 2.0;

  function contextCrop(src, box) {           // src: canvas/video/image; returns a 160x160 canvas
    const cx = box.x + box.w / 2, cy = box.y + box.h / 2, side = Math.max(box.w, box.h) * SCALE;
    const c = document.createElement('canvas'); c.width = c.height = SIZE;
    const g = c.getContext('2d', { willReadFrequently: true });
    // drawImage clamps outside-the-frame areas to transparent; replicate edges like Python by drawing a clamped source
    const sw = src.videoWidth || src.width, sh = src.videoHeight || src.height;
    const sx = cx - side / 2, sy = cy - side / 2;
    g.imageSmoothingQuality = 'high';
    const x0 = Math.max(0, sx), y0 = Math.max(0, sy), x1 = Math.min(sw, sx + side), y1 = Math.min(sh, sy + side);
    // fill with the nearest edge colour (cheap approximation of BORDER_REPLICATE)
    g.drawImage(src, Math.min(Math.max(cx, 0), sw - 1), Math.min(Math.max(cy, 0), sh - 1), 1, 1, 0, 0, SIZE, SIZE);
    g.drawImage(src, x0, y0, x1 - x0, y1 - y0, (x0 - sx) / side * SIZE, (y0 - sy) / side * SIZE,
                (x1 - x0) / side * SIZE, (y1 - y0) / side * SIZE);
    return c;
  }

  function toTensor(canvases) {
    const n = canvases.length, data = new Float32Array(n * 3 * SIZE * SIZE);
    canvases.forEach((cv, i) => {
      const px = cv.getContext('2d').getImageData(0, 0, SIZE, SIZE).data;
      for (let p = 0; p < SIZE * SIZE; p++) for (let ch = 0; ch < 3; ch++)
        data[i * 3 * SIZE * SIZE + ch * SIZE * SIZE + p] = (px[p * 4 + ch] / 255 - MEAN[ch]) / STD[ch];
    });
    return new ort.Tensor('float32', data, [n, 3, SIZE, SIZE]);
  }

  async function load(url) {
    const sess = await ort.InferenceSession.create(url, { executionProviders: ['wasm'] });
    return {
      async score(canvases) {
        const out = await sess.run({ input: toTensor(canvases) });
        const L = out.logits.data, n = canvases.length, mean = new Array(5).fill(0);
        for (let i = 0; i < n; i++) {             // softmax per frame, then average over frames (as in Python)
          const row = Array.from(L.slice(i * 5, i * 5 + 5)), m = Math.max(...row);
          const e = row.map(v => Math.exp(v - m)), s = e.reduce((a, b) => a + b, 0);
          e.forEach((v, k) => mean[k] += v / s / n);
        }
        return { spoof_prob: 1 - mean[0], attack_probs: Object.fromEntries(CLASSES.map((c, k) => [c, mean[k]])) };
      }
    };
  }
  return { load, contextCrop, toTensor, CLASSES };
})();
