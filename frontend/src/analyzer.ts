export type AnalysisResult = {
  metrics: { width: number; height: number; pixel_count: number; red_sum: number; green_sum: number; blue_sum: number };
  lsb: { hex: string; byte_count: number; available_bytes: number; truncated: boolean; discarded_bits: number; bit_order: string };
  anomalies: { file: ScanResult; lsb: ScanResult };
};
type ScanResult = { ascii_ratio: number; strings: { offset: number; text: string }[]; scanned_bytes: number; truncated: boolean };
const MAX_BYTES = 5 * 1024 * 1024;
function scan(data: Uint8Array): ScanResult {
  const sample = data.subarray(0, 2 * 1024 * 1024);
  let printable = 0; let start = -1;
  const strings: { offset: number; text: string }[] = [];
  for (let i = 0; i <= sample.length; i++) {
    if (i < sample.length && sample[i] >= 32 && sample[i] <= 126) { printable++; if (start === -1) start = i; }
    else if (start !== -1) {
      if (i - start >= 6 && strings.length < 20) strings.push({ offset: start, text: String.fromCharCode(...sample.subarray(start, Math.min(i, start + 160))) });
      start = -1;
    }
  }
  return { ascii_ratio: sample.length ? printable / sample.length : 0, strings, scanned_bytes: sample.length, truncated: data.length > sample.length };
}
function dimensions(data: Uint8Array) {
  if (data.length >= 24 && [137, 80, 78, 71, 13, 10, 26, 10].every((n, i) => data[i] === n)) {
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
    if (String.fromCharCode(...data.subarray(12, 16)) !== 'IHDR') throw new Error('Некорректный PNG-заголовок.');
    return { width: view.getUint32(16), height: view.getUint32(20) };
  }
  if (data[0] === 255 && data[1] === 216) {
    let i = 2;
    while (i + 3 < data.length) {
      if (data[i] !== 255) break;
      while (i < data.length && data[i] === 255) i++;
      const marker = data[i++];
      if (marker === 0xda || marker === 0xd9) break;
      if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
      const length = (data[i] << 8) | data[i + 1];
      if (length < 2 || i + length > data.length) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker) && length >= 8) return { height: (data[i + 3] << 8) | data[i + 4], width: (data[i + 5] << 8) | data[i + 6] };
      i += length;
    }
  }
  throw new Error('Файл не распознан как PNG или JPEG. Расширения недостаточно.');
}
export async function analyzeFile(file: File): Promise<AnalysisResult> {
  if (!file.size) throw new Error('Файл пуст. Выберите изображение.');
  if (file.size > MAX_BYTES) throw new Error('Файл больше 5 МиБ. Выберите изображение меньшего размера.');
  const raw = new Uint8Array(await file.arrayBuffer());
  const dim = dimensions(raw);
  if (!dim.width || !dim.height || dim.width * dim.height > 4_000_000) throw new Error('Разрешение превышает 4 миллиона пикселей или некорректно.');
  let bitmap: ImageBitmap;
  try { bitmap = await createImageBitmap(file, { imageOrientation: 'none', premultiplyAlpha: 'none', colorSpaceConversion: 'none' }); }
  catch { throw new Error('Изображение повреждено или не поддерживается декодером браузера.'); }
  try {
    const { width, height } = bitmap;
    if (width * height > 4_000_000) throw new Error('Слишком большое разрешение изображения.');
    const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('Canvas недоступен в этом браузере.');
    ctx.drawImage(bitmap, 0, 0);
    const data = ctx.getImageData(0, 0, width, height).data;
    const sums = [0, 0, 0];
    const available = Math.floor(width * height * 3 / 8);
    const payload = new Uint8Array(Math.min(available, 65536));
    let bitIndex = 0;
    for (let i = 0; i < data.length; i += 4) {
      for (let c = 0; c < 3; c++) {
        const value = data[i + c]; sums[c] += value;
        if (bitIndex < payload.length * 8) payload[Math.floor(bitIndex / 8)] |= (value & 1) << (7 - bitIndex % 8);
        bitIndex++;
      }
    }
    return {
      metrics: { width, height, pixel_count: width * height, red_sum: sums[0], green_sum: sums[1], blue_sum: sums[2] },
      lsb: { hex: Array.from(payload, b => b.toString(16).padStart(2, '0')).join(''), byte_count: payload.length, available_bytes: available, truncated: available > payload.length, discarded_bits: width * height * 3 % 8, bit_order: 'RGB row-major, MSB-first' },
      anomalies: { file: scan(raw), lsb: scan(payload) }
    };
  } finally { bitmap.close(); }
}
export async function makeSample(): Promise<File> {
  const canvas = document.createElement('canvas'); canvas.width = 160; canvas.height = 100;
  const ctx = canvas.getContext('2d'); if (!ctx) throw new Error('Canvas недоступен.');
  const image = ctx.createImageData(160, 100);
  const message = new TextEncoder().encode('STEGOLAB: hello from the hidden layer!');
  let bit = 0;
  for (let i = 0; i < image.data.length; i += 4) {
    const x = (i / 4) % 160, y = Math.floor(i / 4 / 160);
    const color = [100 + Math.floor(x / 3), 130 + Math.floor(y / 3), 80 + Math.floor((x + y) / 5)];
    for (let c = 0; c < 3; c++) { image.data[i + c] = (color[c] & 254) | (bit < message.length * 8 ? (message[Math.floor(bit / 8)] >> (7 - bit % 8)) & 1 : 0); bit++; }
    image.data[i + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(new File([blob], 'stegolab-sample.png', { type: 'image/png' })) : reject(new Error('Не удалось создать пример.')), 'image/png'));
}
