// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AssetLibraryUploadDialog } from '@/features/canvas/ui/AssetLibraryUploadDialog';

const FOLDERS = [
  {
    key: 'other' as const,
    label: '待分类资产',
    items: [],
    system: true,
    uploadable: true,
  },
];

const CATEGORIES = [
  {
    key: 'other' as const,
    labelKey: 'canvas.assetLibrary.category.other',
    media: ['image' as const, 'video' as const],
  },
];

function renderDialog(onSubmit = vi.fn()) {
  render(
    <AssetLibraryUploadDialog
      open
      folders={FOLDERS}
      categories={CATEGORIES}
      onCreateFolder={vi.fn()}
      onSubmit={onSubmit}
      onClose={vi.fn()}
    />,
  );
  return onSubmit;
}

describe('AssetLibraryUploadDialog', () => {
  beforeEach(() => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:preview'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    });
  });

  it('明确展示拖放入口与支持格式', () => {
    renderDialog();

    expect(screen.getByText('拖放文件到这里，或点击选择')).toBeInTheDocument();
    expect(screen.getByText(/jpg\/jpeg\/png\/webp/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上传' })).toBeDisabled();
  });

  it('不支持的文件不会静默消失', () => {
    renderDialog();
    const file = new File(['notes'], 'notes.txt', { type: 'text/plain' });

    fireEvent.change(screen.getByLabelText('选择本地文件'), {
      target: { files: [file] },
    });

    expect(screen.getByRole('alert')).toHaveTextContent(
      '1 个文件格式不受支持，已自动忽略',
    );
  });

  it('显示选择数量，并在选定保存位置后用明确文案提交', () => {
    const onSubmit = renderDialog();
    const file = new File(['image'], 'cover.png', { type: 'image/png' });

    fireEvent.change(screen.getByLabelText('选择本地文件'), {
      target: { files: [file] },
    });
    expect(screen.getByTitle('cover.png')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上传 1 项' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '选择保存位置' }));
    fireEvent.click(screen.getByRole('button', { name: '待分类资产' }));
    fireEvent.click(screen.getByRole('button', { name: '上传 1 项' }));

    // [aipdd fork delta] 标签没手动选过时跟随保存位置：「待分类资产」是类目
    // 同名文件夹，标签跟着落到 other，顶部同名标签下立刻能看到这批素材。
    expect(onSubmit).toHaveBeenCalledWith(
      [{ file, media: 'image' }],
      'other',
      'other',
      null,
    );
  });

  it('保存位置落在「风格」时提供关联风格，提交带上所选风格 id', () => {
    const onSubmit = vi.fn();
    render(
      <AssetLibraryUploadDialog
        open
        folders={[
          ...FOLDERS,
          { key: 'style', label: '风格', items: [], system: true, uploadable: true },
        ]}
        categories={CATEGORIES}
        styleTemplates={[
          {
            id: 'anime',
            label: '动漫',
            category: '动漫',
            cover: 'anime/cover.webp',
            samples: [],
            style_prompt: '动画风',
          },
        ]}
        onCreateFolder={vi.fn()}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );

    const file = new File(['image'], 'cover.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('选择本地文件'), {
      target: { files: [file] },
    });

    // 没落到「风格」前不出现这个入口——普通文件夹传图不该被问要不要改封面。
    expect(screen.queryByText('关联风格')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '选择保存位置' }));
    fireEvent.click(screen.getByRole('button', { name: '风格' }));

    expect(screen.getByText('关联风格')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '选择关联风格' }));
    fireEvent.click(screen.getByRole('button', { name: /动漫/ }));
    fireEvent.click(screen.getByRole('button', { name: '上传 1 项' }));

    expect(onSubmit).toHaveBeenCalledWith(
      [{ file, media: 'image' }],
      'style',
      // 标签没手动选过，跟随保存位置落到「风格」——顶部「风格」标签下可见。
      'style',
      'anime',
    );
  });

  it('标签被手动选过后，换保存位置不再覆盖它', () => {
    const onSubmit = vi.fn();
    render(
      <AssetLibraryUploadDialog
        open
        folders={[
          ...FOLDERS,
          { key: 'style', label: '风格', items: [], system: true, uploadable: true },
        ]}
        categories={[
          ...CATEGORIES,
          {
            key: 'character' as const,
            labelKey: 'canvas.assetLibrary.category.character',
            media: ['image' as const, 'video' as const],
          },
        ]}
        onCreateFolder={vi.fn()}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );
    const file = new File(['image'], 'portrait.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('选择本地文件'), {
      target: { files: [file] },
    });

    // 先手动定标签「人物」……
    fireEvent.click(screen.getByRole('button', { name: '选择标签' }));
    fireEvent.click(screen.getByRole('button', { name: '人物' }));
    // ……再把保存位置改成「风格」：显式选过的标签是用户意志，不被文件夹改写。
    fireEvent.click(screen.getByRole('button', { name: '选择保存位置' }));
    fireEvent.click(screen.getByRole('button', { name: '风格' }));
    fireEvent.click(screen.getByRole('button', { name: '上传 1 项' }));

    expect(onSubmit).toHaveBeenCalledWith(
      [{ file, media: 'image' }],
      'style',
      'character',
      null,
    );
  });

  it('保存位置选「剪映草稿」后进入整文件夹上传模式', () => {
    render(
      <AssetLibraryUploadDialog
        open
        folders={[
          ...FOLDERS,
          {
            key: 'draft' as const,
            label: '剪映草稿',
            items: [],
            system: true,
            uploadable: false,
          },
        ]}
        categories={CATEGORIES}
        onCreateFolder={vi.fn()}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '选择保存位置' }));
    fireEvent.click(screen.getByRole('button', { name: '剪映草稿' }));

    // 草稿是整份文件夹产物：不给标签与文件选择，只收文件夹。
    expect(
      screen.getByText('保存位置是「剪映草稿」：整个文件夹将作为一条草稿上传'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '选择草稿文件夹' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '选择标签' })).toBeNull();
    expect(screen.getByRole('button', { name: '上传草稿' })).toBeDisabled();
  });

  it('选择文件夹后剥掉最外层目录名并整份提交', () => {
    const onSubmitDraft = vi.fn();
    render(
      <AssetLibraryUploadDialog
        open
        folders={[
          ...FOLDERS,
          {
            key: 'draft' as const,
            label: '剪映草稿',
            items: [],
            system: true,
            uploadable: false,
          },
        ]}
        categories={CATEGORIES}
        onCreateFolder={vi.fn()}
        onSubmit={vi.fn()}
        onSubmitDraft={onSubmitDraft}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '选择保存位置' }));
    fireEvent.click(screen.getByRole('button', { name: '剪映草稿' }));

    const withRelativePath = (relativePath: string, name: string) => {
      const file = new File(['x'], name, { type: 'application/octet-stream' });
      Object.defineProperty(file, 'webkitRelativePath', { value: relativePath });
      return file;
    };
    const content = withRelativePath(
      'EP01/draft_content.json',
      'draft_content.json',
    );
    const clip = withRelativePath('EP01/materials/clip.mp4', 'clip.mp4');
    fireEvent.change(screen.getAllByLabelText('选择草稿文件夹')[0], {
      target: { files: [content, clip] },
    });

    expect(screen.getByText(/2 个文件/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '上传草稿' }));

    expect(onSubmitDraft).toHaveBeenCalledTimes(1);
    const [files, folderName] = onSubmitDraft.mock.calls[0];
    expect(folderName).toBe('EP01');
    expect(files).toEqual([
      { path: 'draft_content.json', file: content },
      { path: 'materials/clip.mp4', file: clip },
    ]);
  });
});
