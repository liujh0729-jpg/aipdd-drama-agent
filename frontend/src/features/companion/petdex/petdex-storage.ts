// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// 用户自带 petdex 宠物的本地存储：把 spritesheet.webp（Blob）+ pet.json 元数据存进
// 浏览器 IndexedDB，跨刷新持久。渲染时用 object URL（blob:）—— 生产 CSP 的
// img-src 含 blob:，所以同样能显示，且完全不依赖网络/CDN。

const DB_NAME = "supertale-petdex";
const DB_VERSION = 1;
const STORE = "pets";

export interface StoredPetRecord {
  slug: string;
  displayName: string;
  submittedBy?: string;
  cols?: number;
  rows?: number;
  /** 精灵图原始文件。 */
  blob: Blob;
  addedAt: number;
}

function idbAvailable(): boolean {
  return typeof indexedDB !== "undefined";
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "slug" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function savePetRecord(record: StoredPetRecord): Promise<void> {
  if (!idbAvailable()) throw new Error("IndexedDB unavailable");
  const db = await openDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(record);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export async function getAllPetRecords(): Promise<StoredPetRecord[]> {
  if (!idbAvailable()) return [];
  const db = await openDb();
  try {
    return await new Promise<StoredPetRecord[]>((resolve, reject) => {
      const tx = db.transaction(STORE, "readonly");
      const request = tx.objectStore(STORE).getAll();
      request.onsuccess = () => resolve((request.result as StoredPetRecord[]) ?? []);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

export async function deletePetRecord(slug: string): Promise<void> {
  if (!idbAvailable()) return;
  const db = await openDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(slug);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export interface ImportedPetEntry {
  slug: string;
  displayName: string;
  submittedBy?: string;
  cols?: number;
  rows?: number;
  /** 本会话有效的 object URL（blob:）。 */
  spritesheetUrl: string;
  imported: true;
}

/**
 * 读取所有已导入宠物，并为每只创建一个 object URL 供渲染。object URL 仅当前会话有效，
 * 由调用方在替换/卸载时自行 revoke（原型里交给页面生命周期，量很小）。
 */
export async function loadImportedPets(): Promise<ImportedPetEntry[]> {
  const records = await getAllPetRecords();
  return records
    .sort((a, b) => b.addedAt - a.addedAt)
    .map((record) => ({
      slug: record.slug,
      displayName: record.displayName,
      submittedBy: record.submittedBy,
      cols: record.cols,
      rows: record.rows,
      spritesheetUrl: URL.createObjectURL(record.blob),
      imported: true as const,
    }));
}
