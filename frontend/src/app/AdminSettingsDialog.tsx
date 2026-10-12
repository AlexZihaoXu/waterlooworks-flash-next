import {
  useCallback,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { AlertDialog, Button, CloseButton } from '@heroui/react';
import './AdminSettingsDialog.css';

export type DiscardText = {
  heading: string;
  body: string;
  confirm: string;
  cancel: string;
};
const defaultDiscard: DiscardText = {
  heading: 'Discard unsaved changes?',
  body: 'Typed credentials or keys have not been saved. Closing Admin settings clears them.',
  confirm: 'Discard changes',
  cancel: 'Keep editing',
};

/** Owner-approved shell: HeroUI modal sizes cannot keep compact fixed bounds.
 * Only mounted while open; native modality owns the top layer and focus trap.
 * While `dirty`, every dismissal (Escape, backdrop, close button, footer
 * dismiss) asks for confirmation through a HeroUI AlertDialog portaled inside
 * this native dialog, so it stays in the top layer and interactive.
 */
export function AdminSettingsDialog({
  children,
  notification,
  footer,
  onDismiss,
  dirty = false,
  title = 'Admin settings',
  discard = defaultDiscard,
}: {
  children: ReactNode;
  notification?: ReactNode;
  footer: ReactNode | ((requestDismiss: () => void) => ReactNode);
  onDismiss: () => void;
  dirty?: boolean;
  /** Members reuse the same shell as plain "Settings". */
  title?: string;
  /** Wording for the dismissal confirmation shown while `dirty`. */
  discard?: DiscardText;
}) {
  const headingId = useId();
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const [portal, setPortal] = useState<HTMLDialogElement | null>(null);
  const setDialog = useCallback((node: HTMLDialogElement | null) => {
    dialogRef.current = node;
    setPortal(node);
  }, []);
  const backdropPress = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const requestDismiss = () => {
    if (confirming) return;
    if (dirty) setConfirming(true);
    else onDismiss();
  };

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
      ref={setDialog}
      className="admin-settings-dialog"
      aria-labelledby={headingId}
      onCancel={(event) => {
        event.preventDefault();
        requestDismiss();
      }}
      onClose={(event) => {
        // Ignore a queued StrictMode cleanup event after showModal reopened it.
        // A browser-forced close cannot be vetoed, so it always dismisses.
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
        if (dismiss) requestDismiss();
      }}
    >
      <header className="admin-settings-dialog-header">
        <div className="admin-settings-dialog-heading-row">
          <h2 id={headingId} className="admin-settings-dialog-heading">
            {title}
          </h2>
          <CloseButton aria-label={`Close ${title}`} onPress={requestDismiss} />
        </div>
        {notification}
      </header>
      <div className="admin-settings-dialog-body">{children}</div>
      <footer className="admin-settings-dialog-footer">
        {typeof footer === 'function' ? footer(requestDismiss) : footer}
      </footer>
      {portal && (
        <AlertDialog.Backdrop
          isOpen={confirming}
          onOpenChange={setConfirming}
          UNSTABLE_portalContainer={portal}
        >
          <AlertDialog.Container size="sm">
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Icon status="warning" />
                <AlertDialog.Heading>{discard.heading}</AlertDialog.Heading>
              </AlertDialog.Header>
              <AlertDialog.Body>
                <p>{discard.body}</p>
              </AlertDialog.Body>
              <AlertDialog.Footer>
                <Button slot="close" variant="tertiary">
                  {discard.cancel}
                </Button>
                <Button
                  variant="danger"
                  onPress={() => {
                    setConfirming(false);
                    onDismiss();
                  }}
                >
                  {discard.confirm}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      )}
    </dialog>
  );
}
