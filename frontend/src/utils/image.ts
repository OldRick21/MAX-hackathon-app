// Подготовка аватара: центральный квадрат, 320×320, WebP (или JPEG, если WebP не умеет браузер).
const SIZE = 320;

export async function squareAvatar(file: File): Promise<Blob> {
  if (!file.type.startsWith('image/')) throw new Error('Выберите картинку.');
  const bitmap = await createImageBitmap(file).catch(() => { throw new Error('Не удалось прочитать картинку.'); });
  const side = Math.min(bitmap.width, bitmap.height);
  const canvas = document.createElement('canvas');
  canvas.width = SIZE;
  canvas.height = SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Не удалось обработать картинку.');
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, SIZE, SIZE);
  bitmap.close();
  const encode = (type: string) => new Promise<Blob | null>(r => canvas.toBlob(r, type, 0.86));
  const webp = await encode('image/webp');
  const blob = webp?.type === 'image/webp' ? webp : await encode('image/jpeg');
  if (!blob) throw new Error('Не удалось обработать картинку.');
  return blob;
}
