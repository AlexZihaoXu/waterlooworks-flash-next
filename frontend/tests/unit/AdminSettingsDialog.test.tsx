import { StrictMode, useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AdminSettingsDialog } from '../../src/app/AdminSettingsDialog';

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open settings</button>
      {open && (
        <AdminSettingsDialog
          onDismiss={() => setOpen(false)}
          footer={<button onClick={() => setOpen(false)}>Done</button>}
        >
          <input aria-label="Transient draft" />
        </AdminSettingsDialog>
      )}
    </>
  );
}

function open() {
  const result = render(
    <StrictMode>
      <Harness />
    </StrictMode>,
  );
  const trigger = screen.getByRole('button', { name: 'Open settings' });
  trigger.focus();
  fireEvent.click(trigger);
  return {
    ...result,
    trigger,
    dialog: screen.getByRole('dialog') as HTMLDialogElement,
  };
}

describe('AdminSettingsDialog native lifecycle', () => {
  it.each(['Escape', 'Close Admin settings', 'Done', 'native close'])(
    'unmounts content and restores focus after %s',
    (method) => {
      const { trigger, dialog } = open();
      expect(dialog.open).toBe(true);
      expect(document.body.style.overflow).toBe('hidden');
      const draft = screen.getByLabelText('Transient draft');
      fireEvent.change(draft, { target: { value: crypto.randomUUID() } });
      draft.focus();
      if (method === 'Escape') {
        // Browser Escape emits cancel; jsdom does not implement that default action.
        const cancel = new Event('cancel', { cancelable: true });
        fireEvent(dialog, cancel);
        expect(cancel.defaultPrevented).toBe(true);
      } else if (method === 'native close') {
        dialog.close();
        fireEvent(dialog, new Event('close'));
      } else fireEvent.click(screen.getByRole('button', { name: method }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText('Transient draft'),
      ).not.toBeInTheDocument();
      expect(draft).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
      expect(document.body.style.overflow).toBe('');
      fireEvent.click(trigger);
      expect(screen.getByLabelText('Transient draft')).toHaveValue('');
    },
  );

  it('keeps the same open dialog, focus and drafts across ordinary rerenders with new callbacks', () => {
    const { dialog, rerender } = open();
    const close = vi.spyOn(dialog, 'close');
    const draft = screen.getByLabelText('Transient draft') as HTMLInputElement;
    const value = crypto.randomUUID();
    fireEvent.change(draft, { target: { value } });
    draft.focus();
    rerender(
      <StrictMode>
        <Harness />
      </StrictMode>,
    );
    expect(screen.getByRole('dialog')).toBe(dialog);
    expect(close).not.toHaveBeenCalled();
    expect(draft.value === value).toBe(true);
    expect(draft).toHaveFocus();
    expect(dialog.open).toBe(true);
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('dismisses only a backdrop click that also started outside, not an inside drag or padding click', () => {
    const { dialog } = open();
    vi.spyOn(dialog, 'getBoundingClientRect').mockReturnValue({
      left: 20,
      top: 20,
      right: 400,
      bottom: 400,
    } as DOMRect);
    const down = (x: number) =>
      fireEvent(
        dialog,
        new MouseEvent('pointerdown', {
          bubbles: true,
          clientX: x,
          clientY: x,
        }),
      );
    down(30);
    fireEvent.click(dialog, { clientX: 30, clientY: 30 });
    expect(dialog.open).toBe(true);
    down(30);
    fireEvent.click(dialog, { clientX: 5, clientY: 5 });
    expect(dialog.open).toBe(true);
    down(5);
    fireEvent.click(dialog, { clientX: 5, clientY: 5 });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('survives StrictMode queued close events and closes on unmount without leaking the scroll lock', () => {
    document.body.style.overflow = 'clip';
    const { dialog, unmount, trigger } = open();
    // StrictMode cleanup can enqueue close before the second showModal.
    fireEvent(dialog, new Event('close'));
    expect(dialog.open).toBe(true);
    unmount();
    expect(dialog.open).toBe(false);
    expect(document.body.style.overflow).toBe('clip');
    expect(trigger.isConnected).toBe(false);
    document.body.style.overflow = '';
  });
});

function DirtyHarness() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const dismiss = () => {
    setDraft('');
    setOpen(false);
  };
  return (
    <>
      <button onClick={() => setOpen(true)}>Open settings</button>
      {open && (
        <AdminSettingsDialog
          dirty={draft !== ''}
          onDismiss={dismiss}
          footer={(requestDismiss) => (
            <button onClick={requestDismiss}>Done</button>
          )}
        >
          <input
            aria-label="Transient draft"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </AdminSettingsDialog>
      )}
    </>
  );
}

function openDirty() {
  render(<DirtyHarness />);
  fireEvent.click(screen.getByRole('button', { name: 'Open settings' }));
  const dialog = screen.getByRole('dialog') as HTMLDialogElement;
  fireEvent.change(screen.getByLabelText('Transient draft'), {
    target: { value: 'typed' },
  });
  return dialog;
}

const escape = (dialog: HTMLDialogElement) => {
  const cancel = new Event('cancel', { cancelable: true });
  fireEvent(dialog, cancel);
  expect(cancel.defaultPrevented).toBe(true);
};

describe('AdminSettingsDialog unsaved-draft guard', () => {
  it.each(['Escape', 'Close Admin settings', 'Done', 'backdrop'])(
    'asks before discarding non-empty drafts on %s',
    async (method) => {
      const dialog = openDirty();
      if (method === 'Escape') escape(dialog);
      else if (method === 'backdrop') {
        vi.spyOn(dialog, 'getBoundingClientRect').mockReturnValue({
          left: 20,
          top: 20,
          right: 400,
          bottom: 400,
        } as DOMRect);
        fireEvent(
          dialog,
          new MouseEvent('pointerdown', {
            bubbles: true,
            clientX: 5,
            clientY: 5,
          }),
        );
        fireEvent.click(dialog, { clientX: 5, clientY: 5 });
      } else fireEvent.click(screen.getByRole('button', { name: method }));
      const confirm = await screen.findByRole('alertdialog', {
        name: 'Discard unsaved changes?',
      });
      // Rendered inside the native modal's top layer, so it stays interactive.
      expect(dialog).toContainElement(confirm);
      expect(dialog.open).toBe(true);
      expect(screen.getByLabelText('Transient draft')).toHaveValue('typed');
      fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
      await waitFor(() =>
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument(),
      );
      expect(screen.getByLabelText('Transient draft')).toHaveValue('typed');
      escape(dialog);
      fireEvent.click(
        await screen.findByRole('button', { name: 'Discard changes' }),
      );
      await waitFor(() =>
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
      );
    },
  );

  it('ignores repeated Escape while the confirmation is open', async () => {
    const dialog = openDirty();
    escape(dialog);
    await screen.findByRole('alertdialog');
    escape(dialog);
    expect(screen.getAllByRole('alertdialog')).toHaveLength(1);
    expect(dialog.open).toBe(true);
  });

  it('closes immediately once drafts are empty', () => {
    const dialog = openDirty();
    fireEvent.change(screen.getByLabelText('Transient draft'), {
      target: { value: '' },
    });
    escape(dialog);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
