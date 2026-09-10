import { useId, useLayoutEffect, useRef, type ReactNode } from 'react';
import { CloseButton } from '@heroui/react';
import './AdminSettingsDialog.css';

/** Owner-approved shell: HeroUI modal sizes cannot keep compact fixed bounds.
 * Only mounted while open; native modality owns the top layer and focus trap.
 */
export function AdminSettingsDialog({
  children,
  notification,
  footer,
  onDismiss,
}: {
  children: ReactNode;
  notification?: ReactNode;
  footer: ReactNode;
  onDismiss: () => void;
}) {
  const headingId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const backdropPress = useRef(false);

  useLayoutEffect(() => {
    const dialog = dialogRef.current!;
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      if (dialog.open) dialog.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected)
        previousFocus.focus({ preventScroll: true });
    };
  }, []);

  const isBackdrop = (event: React.MouseEvent<HTMLDialogElement>) => {
    if (event.target !== event.currentTarget) return false;
    const rect = event.currentTarget.getBoundingClientRect();
    return (
      event.clientX < rect.left ||
      event.clientX > rect.right ||
      event.clientY < rect.top ||
      event.clientY > rect.bottom
    );
  };

  return (
    <dialog
      ref={dialogRef}
      className="admin-settings-dialog"
      aria-labelledby={headingId}
      onCancel={(event) => {
        event.preventDefault();
        onDismiss();
      }}
      onClose={(event) => {
        // Ignore a queued StrictMode cleanup event after showModal reopened it.
        if (!event.currentTarget.open) onDismiss();
      }}
      onPointerDown={(event) => {
        backdropPress.current = isBackdrop(event);
      }}
      onPointerCancel={() => {
        backdropPress.current = false;
      }}
      onClick={(event) => {
        const dismiss = backdropPress.current && isBackdrop(event);
        backdropPress.current = false;
        if (dismiss) onDismiss();
      }}
    >
      <header className="admin-settings-dialog-header">
        <div className="admin-settings-dialog-heading-row">
          <h2 id={headingId} className="admin-settings-dialog-heading">
            Admin settings
          </h2>
          <CloseButton aria-label="Close Admin settings" onPress={onDismiss} />
        </div>
        {notification}
      </header>
      <div className="admin-settings-dialog-body">{children}</div>
      <footer className="admin-settings-dialog-footer">{footer}</footer>
    </dialog>
  );
}
