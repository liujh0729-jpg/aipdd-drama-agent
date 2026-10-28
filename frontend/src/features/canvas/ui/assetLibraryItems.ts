// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
//
// 资产库的条目模型与类目定义。弹窗(AssetLibraryModal，选参考图用)和 Freezone 左侧
// 面板的「资产库」tab 共用这里，类目和归一化逻辑只有一份，避免两边漂移。
// 这里只放纯数据推导，调接口的写操作(上传/改名/删除/同步)一律留在各自组件里。
import type {
  FreezoneAssetLibraryCategory,
  FreezoneAssetLibraryFolder,
  FreezoneAssetLibraryMedia,
  FreezoneAssetLibrarySource,
  FreezoneUploadableMedia,
} from '@/api/ops';
import type { TFn } from '@/lib/i18n-types';

export const ASSET_LIBRARY_CARD_CLASS =
  'overflow-hidden rounded-sm border border-border bg-muted/30 transition-colors';
export const ASSET_LIBRARY_CARD_HOVER_CLASS =
  'hover:border-primary/35 hover:bg-accent';

export type AssetLibraryMedia = FreezoneAssetLibraryMedia;
/** 可作为上传媒介的类型：剪映草稿（draft）只能由 VectCut 生成，不走上传链路。 */
export type UploadableMedia = FreezoneUploadableMedia;

export interface LibraryItem {
  id: string | null;
  name: string;
  media: AssetLibraryMedia;
  source: FreezoneAssetLibrarySource;
  /** 用途类目（标签）。老条目后端不返回时按 source/media 兜底推导，见 deriveCategory。 */
  category: AssetCategory;
  /** 保存位置（文件夹 key）。老条目没有该字段时按类目归位，见 deriveFolder。 */
  folder: AssetFolderKey;
  /** 该条目在其 media 类型下的主展示 / 引用地址。 */
  url: string;
  raw: Record<string, unknown>;
}

/** 资产名长度上限，与后端 LIBRARY_ITEM_NAME_MAX_LEN 一致。 */
export const LIBRARY_ITEM_NAME_MAX_LEN = 60;

/**
 * 设置→风格管理里已配封面的风格会在「风格」栏目里以只读虚拟条目出现（后端读取
 * 时投影，不入库文件），id 带该前缀——改名/删除入口据此拦下。与后端
 * video_node.py 的 STYLE_COVER_ITEM_PREFIX 一致。
 */
export const STYLE_COVER_ITEM_PREFIX = 'style_gallery:';

/** 条目是否来自「设置→风格管理」的封面投影（只读，只能去设置里更换/删除）。 */
export function isStyleCoverItem(entry: Pick<LibraryItem, 'id'>): boolean {
  return (
    typeof entry.id === 'string' && entry.id.startsWith(STYLE_COVER_ITEM_PREFIX)
  );
}

const FILE_EXTENSION_PATTERN = /\.([a-z0-9]{2,5})$/i;
const MEDIA_FALLBACK_EXTENSION: Record<AssetLibraryMedia, string> = {
  image: 'png',
  video: 'mp4',
  audio: 'mp3',
  draft: 'zip',
  model3d: 'glb',
  text: 'txt',
};

/**
 * 下载时给素材起的文件名：资产名 + 从 URL 推出来的后缀。
 *
 * 资产名是用户随手填的，可能带 `/`、`\`、`:` 之类会被系统当路径分隔符的字符，
 * 一律换成 `_`；名字整个被洗空时退回媒介名，总之不能生成一个空文件名。
 */
export function libraryItemDownloadFilename(entry: LibraryItem): string {
  const path = entry.url.split(/[?#]/, 1)[0] ?? '';
  const extension =
    FILE_EXTENSION_PATTERN.exec(path)?.[1]?.toLowerCase() ??
    MEDIA_FALLBACK_EXTENSION[entry.media];
  const base =
    entry.name.replace(/[\\/:*?"<>|]+/g, '_').trim() || entry.media;
  // 名字自己就带对后缀时不再叠一层（「封面.png」不该下成「封面.png.png」）。
  return base.toLowerCase().endsWith(`.${extension}`)
    ? base
    : `${base}.${extension}`;
}

/**
 * 资产类目。分类不再按媒介（图片/视频/音频）切，而是按用途切——同一个类目下
 * 图片、视频、音频都可能有。`audio`（音效）是唯一天然只装音频的类目。
 */
export type AssetCategory = FreezoneAssetLibraryCategory;

export interface AssetCategoryDef {
  key: AssetCategory;
  /** 展示名的词条 key——这张表跟着界面语言走，源码里只留 key。 */
  labelKey: string;
  /** 该类目收哪些媒介的文件——决定上传时的 accept 与提示文案。 */
  media: AssetLibraryMedia[];
}

export const ASSET_CATEGORIES: AssetCategoryDef[] = [
  { key: 'other', labelKey: 'canvas.assetLibrary.category.other', media: ['image', 'video'] },
  {
    key: 'character',
    labelKey: 'canvas.assetLibrary.category.character',
    media: ['image', 'video'],
  },
  { key: 'scene', labelKey: 'canvas.assetLibrary.category.scene', media: ['image', 'video'] },
  { key: 'prop', labelKey: 'canvas.assetLibrary.category.prop', media: ['image', 'video'] },
  {
    key: 'sketch',
    labelKey: 'canvas.assetLibrary.category.sketch',
    media: ['image'],
  },
  // [aipdd fork delta] 「俯视图」类目：存画布俯视图设计导出的预览图（media=image），
  // 并随条目内联一份可编辑场景 raw.scene；从资产库送回画布时据此生成新俯视图节点。
  { key: 'topdown', labelKey: 'canvas.assetLibrary.category.topdown', media: ['image'] },
  // [aipdd fork delta] 「故事版」类目：分镜脚本页 AI 生成的多格分镜板参考图（media=image），
  // 镜头生成视频 / 草图生成渲染图选参考图时可取用。
  {
    key: 'storyVersion',
    labelKey: 'canvas.assetLibrary.category.storyVersion',
    media: ['image'],
  },
  { key: 'style', labelKey: 'canvas.assetLibrary.category.style', media: ['image', 'video'] },
  { key: 'audio', labelKey: 'canvas.assetLibrary.category.audio', media: ['audio'] },
  // [aipdd fork delta] 「剪映草稿」类目：只收 VectCut 产物（media="draft"），不接受
  // 上传，所以 media 为空数组——上传对话框据此把它从可传标签里排除。
  { key: 'draft', labelKey: 'canvas.assetLibrary.category.draft', media: [] },
  // [aipdd fork delta req98] 「3D」「文本」类目：只收生成产物（media="model3d"/"text"），
  // 与剪映草稿一样不接受上传（media 为空数组）。
  { key: 'model3d', labelKey: 'canvas.assetLibrary.category.model3d', media: [] },
  { key: 'text', labelKey: 'canvas.assetLibrary.category.text', media: [] },
];

/** 类目筛选器的「全部类别」key。文件夹范围由独立导航控制，不再混入同一排。 */
export const ALL_CATEGORY_KEY = 'all';
export type AssetLibraryCategoryFilterKey =
  | typeof ALL_CATEGORY_KEY
  | AssetCategory;

/** 从 File 的 MIME 判断它属于哪种媒介；认不出来返回 null（直接丢弃该文件）。 */
export function mediaOfFile(file: File): UploadableMedia | null {
  if (file.type.startsWith('image/')) return 'image';
  if (file.type.startsWith('video/')) return 'video';
  if (file.type.startsWith('audio/')) return 'audio';
  return null;
}

const CATEGORY_KEYS = new Set<string>(ASSET_CATEGORIES.map((c) => c.key));

/**
 * 老条目没有 category 字段，按它的来源/媒介兜底推一个：主线同步来的人物/场景/
 * 道具直接对号入座，音频归音效，剩下的本地上传归「其它」。
 */
export function deriveCategory(
  raw: unknown,
  source: FreezoneAssetLibrarySource,
  media: AssetLibraryMedia,
): AssetCategory {
  if (typeof raw === 'string' && CATEGORY_KEYS.has(raw)) {
    return raw as AssetCategory;
  }
  if (source === 'character' || source === 'scene' || source === 'prop') {
    return source;
  }
  if (media === 'draft') return 'draft';
  // 与后端 _resolve_library_category 同口径：3D / 文本载体对号入座同名标签。
  if (media === 'model3d' || media === 'text') return media;
  return media === 'audio' ? 'audio' : 'other';
}

export const SOURCE_LABEL_KEYS: Record<FreezoneAssetLibrarySource, string> = {
  upload: 'canvas.assetLibrary.source.upload',
  character: 'canvas.assetLibrary.source.character',
  scene: 'canvas.assetLibrary.source.scene',
  prop: 'canvas.assetLibrary.source.prop',
};

/**
 * 文件夹（保存位置）和类目（标签）是两个独立维度：类目只说「这素材是干嘛的」，
 * 文件夹说「它放在哪」。key 的取值有三种——
 *   - `mainline`：主线同步来的资产的固定去处，不接受上传；
 *   - 类目 key（other/character/…）：同名系统文件夹，老条目没有 folder 字段时
 *     按类目落进来，所以不需要数据迁移；
 *   - 其余：用户自建文件夹的 id（后端随机生成，不会和上面撞）。
 * 与后端 video_node.py 的 RESERVED_FOLDER_KEYS / _resolve_library_folder 对应。
 */
export type AssetFolderKey = string;

export const MAINLINE_FOLDER_KEY = 'mainline';
// [aipdd fork delta] 「剪映草稿」系统文件夹 key（与类目 key 同名，也是后端
// DRAFT_FOLDER_KEY）。它是常驻文件夹：上传文件夹形式的草稿与「同步草稿目录」
// 都落在这里，即使一份草稿都没有也要有个能点进去的位置。
export const DRAFT_FOLDER_KEY = 'draft';
/** 没归类的本地上传落在「其它」类目，文件夹上换个更直白的名字。 */
export const UNSORTED_FOLDER_LABEL_KEY = 'canvas.assetLibrary.uncategorized';
/** 与后端 FOLDER_NAME_MAX_LEN 一致。 */
export const FOLDER_NAME_MAX_LEN = 20;

const SYSTEM_FOLDER_LABEL_KEYS: Record<string, string> = {
  [MAINLINE_FOLDER_KEY]: 'canvas.assetLibrary.folder.mainline',
  ...Object.fromEntries(
    ASSET_CATEGORIES.map((category) => [
      category.key,
      category.key === 'other' ? UNSORTED_FOLDER_LABEL_KEY : category.labelKey,
    ]),
  ),
};

/** 系统文件夹显示名的词条 key；不是系统 key（自建文件夹的 id）时返回 null。 */
export function systemFolderLabelKey(key: AssetFolderKey): string | null {
  return SYSTEM_FOLDER_LABEL_KEYS[key] ?? null;
}

/**
 * 用户建不出来的名字（会和系统文件夹撞），与后端 RESERVED_FOLDER_NAMES 一致。
 * 这是跟后端对齐的协议值，不是界面文案，所以写死中文、不入词条。
 */
// i18n-exempt-start
export const RESERVED_FOLDER_NAMES = [
  '主线',
  '待分类资产',
  '其它',
  '人物',
  '场景',
  '物品',
  '草图',
  '风格',
  '音效',
  '剪映草稿',
  '俯视图',
  '故事版',
  '3D',
  '文本',
];
// i18n-exempt-end

export interface AssetFolder {
  key: AssetFolderKey;
  label: string;
  items: LibraryItem[];
  /** 系统文件夹不可重命名/删除/改封面——它们是按标签派生的，没有实体记录。 */
  system: boolean;
  /** 能否作为上传的保存位置。主线是同步产物，只读。 */
  uploadable: boolean;
  /** 用户设的封面图 URL；没设过时按文件夹里第一张图兜底，都没有就画图标。 */
  cover?: string | null;
  /** 建夹时间（ISO）。系统文件夹是按标签派生的，没有这个字段。 */
  createdAt?: string | null;
}

/** 建夹日期，只取到天：卡片上够用，也免得时区把时分秒显示歪。 */
export function formatFolderDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** 文件夹封面：用户指定的优先，否则拿文件夹里第一张图顶上（音频没有可用画面）。 */
export function folderCoverUrl(folder: AssetFolder): string | null {
  if (folder.cover) return folder.cover;
  const firstImage = folder.items.find(
    (entry) => entry.media === 'image' && entry.url,
  );
  return firstImage?.url ?? null;
}

/** 老条目没有 folder 字段：主线的进主线，本地上传的按类目进同名系统文件夹。 */
export function deriveFolder(
  raw: unknown,
  source: FreezoneAssetLibrarySource,
  category: AssetCategory,
): AssetFolderKey {
  if (typeof raw === 'string' && raw) return raw;
  return source === 'upload' ? category : MAINLINE_FOLDER_KEY;
}

/**
 * 恒显的系统文件夹：库空的时候也得有地方可点、可上传。【待分类资产】是上传的
 * 默认去处；【风格】是画布「选择风格」图墙封面的来源（上传对话框里选它就会
 * 出现「关联风格」），一条图都没有时同样要能往里传。
 */
const ALWAYS_PRESENT_FOLDER_KEYS: ReadonlySet<AssetCategory> = new Set([
  'other',
  'style',
  // [aipdd fork delta] 「剪映草稿」常驻：草稿文件夹上传与目录同步都需要一个稳定
  // 入口，空库时也要能在左侧导航看到它。
  'draft',
]);

/**
 * 把条目摆进文件夹。【主线】与恒显集合（见上）一直出现，其余系统文件夹有内容
 * 才出现；自建文件夹一律出现（刚建完还是空的也要看得见）。
 *
 * [aipdd fork delta] 「剪映草稿」的 uploadable 保持 false：普通文件（拖拽/单文件）
 * 不能传进去；它的上传是文件夹模式，由上传对话框单独处理（见
 * AssetLibraryUploadDialog 的 draft 分支），拖拽路径据此保持拒绝。
 */
export function buildAssetFolders(
  items: LibraryItem[],
  t: TFn,
  customFolders: FreezoneAssetLibraryFolder[] = [],
): AssetFolder[] {
  const own = (key: AssetFolderKey) =>
    items.filter((entry) => entry.folder === key);
  const folders: AssetFolder[] = [
    {
      key: MAINLINE_FOLDER_KEY,
      label: t(SYSTEM_FOLDER_LABEL_KEYS[MAINLINE_FOLDER_KEY]),
      items: items.filter(
        (entry) =>
          entry.folder === MAINLINE_FOLDER_KEY || entry.source !== 'upload',
      ),
      system: true,
      uploadable: false,
    },
  ];
  for (const category of ASSET_CATEGORIES) {
    const owned = own(category.key);
    if (!ALWAYS_PRESENT_FOLDER_KEYS.has(category.key) && owned.length === 0) {
      continue;
    }
    folders.push({
      key: category.key,
      label: t(SYSTEM_FOLDER_LABEL_KEYS[category.key]),
      items: owned,
      system: true,
      // media 为空的类目（剪映草稿）不接受上传，只能由 VectCut 产物带进来。
      uploadable: category.media.length > 0,
    });
  }
  for (const folder of customFolders) {
    folders.push({
      key: folder.id,
      label: folder.name,
      items: own(folder.id),
      system: false,
      uploadable: true,
      cover: folder.cover ?? null,
      createdAt: folder.created_at ?? null,
    });
  }
  return folders;
}

function itemUrl(media: AssetLibraryMedia, it: Record<string, unknown>): string {
  if (media === 'video') return typeof it.video_url === 'string' ? it.video_url : '';
  if (media === 'audio') return typeof it.audio_url === 'string' ? it.audio_url : '';
  // 剪映草稿没有可直接预览的文件 URL，只承载 draft_id（下载走草稿包代理端点）。
  if (media === 'draft') return '';
  // [aipdd fork delta req98] 3D 模型 / txt 文档的文件地址字段。
  if (media === 'model3d') {
    return typeof it.model3d_url === 'string' ? it.model3d_url : '';
  }
  if (media === 'text') {
    return typeof it.text_url === 'string' ? it.text_url : '';
  }
  const urls = it.image_urls ?? it.imageUrls ?? it.images;
  if (Array.isArray(urls)) {
    const first = urls.find((u): u is string => typeof u === 'string');
    if (first) return first;
  }
  return typeof it.cover_url === 'string' ? it.cover_url : '';
}

/**
 * 草稿条目在 VectCut 服务里的 draft_id。没有这个字段（非草稿条目 / 老数据）返回
 * 空串，调用方据此禁用下载入口，而不是拼出一个必然 404 的地址。
 */
export function draftIdOf(entry: LibraryItem): string {
  const raw = entry.raw ?? {};
  const value = raw.draft_id ?? raw.draftId;
  return typeof value === 'string' ? value.trim() : '';
}

// [aipdd fork delta] 俯视图草稿条目内联的可编辑场景（raw.scene）；非俯视图条目返回
// undefined，调用方据此走普通图片发送链路。场景回载/规整交给 topdown/sceneModel。
export function topdownSceneOf(entry: LibraryItem): unknown {
  if (entry.category !== 'topdown') return undefined;
  return entry.raw?.scene;
}

export function normalizeLibraryList(payload: unknown): LibraryItem[] {
  let arr: unknown[] = [];
  if (Array.isArray(payload)) {
    arr = payload;
  } else if (payload && typeof payload === 'object') {
    const rec = payload as Record<string, unknown>;
    for (const key of ['items', 'data', 'characters', 'list', 'records']) {
      if (Array.isArray(rec[key])) {
        arr = rec[key] as unknown[];
        break;
      }
    }
  }
  return arr
    .filter(
      (it): it is Record<string, unknown> =>
        Boolean(it && typeof it === 'object' && !Array.isArray(it)),
    )
    .map((it) => {
      const idRaw = it.id ?? it.item_id ?? it.itemId ?? null;
      const id =
        typeof idRaw === 'string' ? idRaw : idRaw != null ? String(idRaw) : null;
      const name = typeof it.name === 'string' ? it.name : '';
      // 缺省 image 兼容老数据（历史条目没有 media 字段）。
      const mediaRaw = typeof it.media === 'string' ? it.media : 'image';
      const media: AssetLibraryMedia =
        mediaRaw === 'video' ||
        mediaRaw === 'audio' ||
        mediaRaw === 'draft' ||
        mediaRaw === 'model3d' ||
        mediaRaw === 'text'
          ? mediaRaw
          : 'image';
      const sourceRaw = typeof it.source === 'string' ? it.source : 'upload';
      const source: FreezoneAssetLibrarySource =
        sourceRaw === 'character' ||
        sourceRaw === 'scene' ||
        sourceRaw === 'prop'
          ? sourceRaw
          : 'upload';
      const category = deriveCategory(it.category, source, media);
      return {
        id,
        name,
        media,
        source,
        category,
        folder: deriveFolder(it.folder, source, category),
        url: itemUrl(media, it),
        raw: it,
      };
    })
    // 剪映草稿条目没有直接可预览的 URL（只承载 draft_id），不能按空 URL 筛掉。
    .filter((it) => Boolean(it.url) || it.media === 'draft');
}
