import { useCallback, useEffect, useRef, useState } from 'react';
import { EditorContent, useEditor, useEditorState } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import Placeholder from '@tiptap/extension-placeholder';
import { TableKit } from '@tiptap/extension-table';
import { Bold, Heading2, Italic, List, ListOrdered, Plus, Quote, Trash2 } from 'lucide-react';
import { api, relative, type AppState, type Page } from '../api';

function Editor({ page, onSaved }: { page: Page; onSaved: (page: Page) => void }) {
  const [title, setTitle] = useState(page.title);
  const [status, setStatus] = useState<'saved' | 'dirty' | 'saving' | 'conflict' | 'error'>('saved');
  const revision = useRef(page.revision);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const editor = useEditor({
    extensions: [
      StarterKit,
      TableKit.configure({ table: { resizable: false } }),
      Markdown,
      Placeholder.configure({ placeholder: 'Write here. Your Dots read and edit this page too.' }),
    ],
    content: page.content,
    contentType: 'markdown',
    onUpdate: () => {
      setStatus('dirty');
      clearTimeout(timer.current);
      timer.current = setTimeout(() => void save(), 1200);
    },
  });
  const active = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e?.isActive('bold') ?? false,
      italic: e?.isActive('italic') ?? false,
      h2: e?.isActive('heading', { level: 2 }) ?? false,
      bullet: e?.isActive('bulletList') ?? false,
      ordered: e?.isActive('orderedList') ?? false,
      quote: e?.isActive('blockquote') ?? false,
    }),
  });

  const save = useCallback(
    async (nextTitle?: string) => {
      if (!editor) return;
      setStatus('saving');
      try {
        const saved = await api<Page>(`/pages/${page.id}`, 'PATCH', {
          expected_revision: revision.current,
          content: editor.getMarkdown(),
          title: (nextTitle ?? title).trim() || page.title,
        });
        revision.current = saved.revision;
        setStatus('saved');
        onSaved(saved);
      } catch (e) {
        setStatus(e instanceof Error && e.message.includes('changed since') ? 'conflict' : 'error');
      }
    },
    [editor, page.id, page.title, title, onSaved],
  );

  useEffect(() => () => clearTimeout(timer.current), []);

  const tool = (label: string, pressed: boolean, run: () => void, icon: React.ReactNode) => (
    <button type="button" aria-label={label} title={label} aria-pressed={pressed} onClick={run}>
      {icon}
    </button>
  );
  return (
    <div className="stack">
      <div className="row">
        <input
          className="editor-title grow"
          aria-label="Page title"
          value={title}
          maxLength={160}
          onChange={(e) => {
            setTitle(e.target.value);
            setStatus('dirty');
          }}
          onBlur={() => status === 'dirty' && void save(title)}
        />
        <span className="muted small" role="status">
          {status === 'saved'
            ? `Saved, revision ${revision.current}`
            : status === 'saving'
              ? 'Saving…'
              : status === 'dirty'
                ? 'Unsaved changes'
                : status === 'conflict'
                  ? 'A Dot changed this page. Reload to see their version.'
                  : 'Could not save. Check the server.'}
        </span>
      </div>
      <div className="editor">
        <div className="toolbar" aria-label="Formatting">
          {tool('Bold', active?.bold ?? false, () => editor?.chain().focus().toggleBold().run(), <Bold size={15} />)}
          {tool('Italic', active?.italic ?? false, () => editor?.chain().focus().toggleItalic().run(), <Italic size={15} />)}
          {tool('Heading', active?.h2 ?? false, () => editor?.chain().focus().toggleHeading({ level: 2 }).run(), <Heading2 size={15} />)}
          {tool('Bulleted list', active?.bullet ?? false, () => editor?.chain().focus().toggleBulletList().run(), <List size={15} />)}
          {tool('Numbered list', active?.ordered ?? false, () => editor?.chain().focus().toggleOrderedList().run(), <ListOrdered size={15} />)}
          {tool('Quote', active?.quote ?? false, () => editor?.chain().focus().toggleBlockquote().run(), <Quote size={15} />)}
        </div>
        <EditorContent editor={editor} />
      </div>
      <p className="muted small">
        Last edited by {page.author} {relative(page.updated_at)}. Every save keeps a revision.
      </p>
    </div>
  );
}

export function PagesView({
  state,
  spaceId,
  pageId,
  navigate,
}: {
  state: AppState;
  spaceId?: string;
  pageId?: string;
  navigate: (path: string) => void;
}) {
  const space = state.spaces.find((s) => s.id === spaceId) ?? state.spaces[0];
  const [pages, setPages] = useState<Page[]>([]);
  const [page, setPage] = useState<Page>();
  const [error, setError] = useState('');
  const loadPages = useCallback(async () => {
    if (!space) return;
    setPages(await api<Page[]>(`/spaces/${space.id}/pages`));
  }, [space]);
  useEffect(() => {
    void loadPages().catch((e) => setError(e.message));
  }, [loadPages]);
  useEffect(() => {
    setPage(undefined);
    if (pageId) void api<Page>(`/pages/${pageId}`).then(setPage).catch((e) => setError(e.message));
  }, [pageId]);
  if (!space) return <div className="view">No Spaces yet.</div>;
  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{space.name}</h1>
          <p className="muted">{space.description || 'Pages your team reads and writes.'}</p>
        </div>
        <div className="row">
          {state.spaces.length > 1 && (
            <select aria-label="Space" value={space.id} onChange={(e) => navigate(`/pages/${e.target.value}`)}>
              {state.spaces.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          )}
          <button
            className="button primary"
            onClick={async () => {
              const created = await api<Page>(`/spaces/${space.id}/pages`, 'POST', { title: 'Untitled', content: '' });
              await loadPages();
              navigate(`/pages/${space.id}/${created.id}`);
            }}
          >
            <Plus size={16} /> New page
          </button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="pages-layout">
        <nav className="page-list" aria-label="Pages">
          {pages.map((p) => (
            <button key={p.id} aria-current={p.id === pageId} onClick={() => navigate(`/pages/${space.id}/${p.id}`)}>
              {p.title}
              <br />
              <span className="muted small">
                {p.author}, {relative(p.updated_at)}
              </span>
            </button>
          ))}
          {!pages.length && <p className="muted small">No pages yet.</p>}
        </nav>
        <div>
          {page ? (
            <>
              <Editor
                key={page.id}
                page={page}
                onSaved={(saved) => {
                  setPages((current) => current.map((p) => (p.id === saved.id ? { ...p, ...saved } : p)));
                }}
              />
              <button
                className="button ghost danger"
                style={{ marginTop: 12 }}
                onClick={async () => {
                  if (!window.confirm(`Delete “${page.title}”?`)) return;
                  await api(`/pages/${page.id}`, 'DELETE');
                  await loadPages();
                  navigate(`/pages/${space.id}`);
                }}
              >
                <Trash2 size={15} /> Delete page
              </button>
            </>
          ) : (
            <div className="empty">
              <h2>Pick a page</h2>
              <p>Pages are the team's shared memory: briefs, approved messaging, staging tables, reports.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
