import { StrictMode, useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
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
