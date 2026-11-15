import type { ChatConfig } from "@/lib/api/types";

export const DOCUMENT_EXTENSIONS = ["pdf", "docx", "pptx", "xlsx", "txt", "md", "markdown", "csv"];
export const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "webp"];
export const IMAGE_KINDS = ["png", "jpg", "webp"];

export const DOCUMENT_TYPES_LABEL = "PDF, DOCX, PPTX, XLSX, TXT, MD and CSV";
export const IMAGE_TYPES_LABEL = "PNG, JPG and WEBP";

export const formatSize = (bytes: number) =>
  bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

const extensionOf = (name: string) => (name.includes(".") ? name.slice(name.lastIndexOf(".") + 1).toLowerCase() : "");

/** The `accept` list for the file picker. */
export const acceptedExtensions = (imagesEnabled: boolean) =>
  [...DOCUMENT_EXTENSIONS, ...(imagesEnabled ? IMAGE_EXTENSIONS : [])].map((e) => `.${e}`).join(",");

/**
 * Refuses in the browser what the API would refuse anyway, with a reason the person can act on. Limits the
 * browser is not told about (document size) are left to the API's own message.
 */
export function checkFile(file: File, config: Partial<ChatConfig> | undefined, alreadyPending: number): string | null {
  const imagesEnabled = config?.images_enabled === true;
  const ext = extensionOf(file.name);
  const isImage = IMAGE_EXTENSIONS.includes(ext);
  if (config?.attachments_per_message && alreadyPending >= config.attachments_per_message) {
    return `"${file.name}" wasn't attached. At most ${config.attachments_per_message} files can go with one message: send this message or remove one first.`;
  }
  if (isImage) {
    if (!imagesEnabled) {
      return `"${file.name}" is an image, and images can't be attached here. Attach a document instead (${DOCUMENT_TYPES_LABEL}).`;
    }
    const limit = config?.image_max_mb;
    if (limit && file.size > limit * 1024 * 1024) {
      return `"${file.name}" is too large: an image can be at most ${limit} MB.`;
    }
    return null;
  }
  if (!DOCUMENT_EXTENSIONS.includes(ext)) {
    const accepted = imagesEnabled
      ? `${DOCUMENT_TYPES_LABEL}, and images (${IMAGE_TYPES_LABEL})`
      : DOCUMENT_TYPES_LABEL;
    return `"${file.name}" isn't a supported type. You can attach ${accepted}.`;
  }
  return null;
}

/** The API's reason, in the person's words: setting names are for operators, not for them. */
export function uploadError(file: File, err: unknown): string {
  const reason = (err instanceof Error ? err.message : "The upload failed.")
    .replace(/\s*\(CHAT__[A-Z_]+\)/g, "")
    .trim();
  return `"${file.name}" couldn't be attached. ${reason}`;
}
