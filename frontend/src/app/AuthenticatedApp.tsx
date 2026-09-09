import type { ReactNode } from 'react';
import { FcGoogle } from 'react-icons/fc';
import { LuUserRound } from 'react-icons/lu';
import { Alert, Avatar, Button, Card, Dropdown, Label } from '@heroui/react';
import { googlePhotoUrl, type AccountProfile } from '../lib/accountProfile';
import { useAccountProfile } from '../lib/useAccountProfile';
import type { AuthState } from '../lib/authLifecycle';
import { useAppAuth } from '../lib/useAppAuth';
import { App } from './App';

// Documented HeroUI Card/Button/Alert composition; no custom visual widget.
export function AuthGate({
  state,
  signIn,
  children,
}: {
  state: AuthState;
  signIn: () => void;
  children: ReactNode;
}) {
  if (state.status === 'ready') return children;
  const pending = state.status === 'checking' || state.status === 'refreshing';
  return (
    <App
      key="signed-out"
      state={{ status: 'signed-out' }}
      signedOutContent={
        <Card>
          <Card.Header>
            <Card.Title>Sign in to continue</Card.Title>
            <Card.Description>
              Sign in with Google to browse shared saved jobs. Only the
              administrator can refresh them.
            </Card.Description>
          </Card.Header>
          <Card.Content>
            {state.status === 'error' && (
              <Alert status="danger">
                <Alert.Indicator />
                <Alert.Content>
                  <Alert.Title>Authentication unavailable</Alert.Title>
                  <Alert.Description>
                    Sign-in or sign-out could not be confirmed. Check your
                    connection and try again. Saved jobs are hidden.
                  </Alert.Description>
                </Alert.Content>
              </Alert>
            )}
            {state.status === 'refreshing' && <p>Renewing your session…</p>}
          </Card.Content>
          <Card.Footer>
            <Button
              variant="secondary"
              onPress={signIn}
              isPending={pending}
              isDisabled={pending}
              fullWidth
            >
              <FcGoogle aria-hidden="true" />
              Sign in with Google
            </Button>
          </Card.Footer>
        </Card>
      }
    />
  );
}

// Documented HeroUI composition; native spans only lay out the account text.
export function AccountMenu({
  profile,
  signOut,
}: {
  profile: AccountProfile | null;
  signOut: () => void;
}) {
  const photo = googlePhotoUrl(profile?.photo_url);
  return (
    <Dropdown>
      <Button aria-label="Account menu" variant="ghost" size="lg">
        <Avatar size="sm">
          {photo && (
            <Avatar.Image src={photo} alt="" referrerPolicy="no-referrer" />
          )}
          <Avatar.Fallback>
            <LuUserRound aria-hidden="true" />
          </Avatar.Fallback>
        </Avatar>
        <span className="account-text">
          <span className="account-name">
            {profile?.display_name || 'Account'}
          </span>
          {profile?.email && (
            <span className="account-email">{profile.email}</span>
          )}
        </span>
      </Button>
      <Dropdown.Popover placement="bottom end">
        <Dropdown.Menu
          aria-label="Account actions"
          onAction={(key) => {
            if (key === 'sign-out') signOut();
          }}
        >
          <Dropdown.Item id="sign-out" textValue="Sign out">
            <Label>Sign out</Label>
          </Dropdown.Item>
        </Dropdown.Menu>
      </Dropdown.Popover>
    </Dropdown>
  );
}

export function AuthenticatedApp() {
  const { state, signIn, signOut } = useAppAuth();
  const profile = useAccountProfile(
    state.status === 'ready' ? state.session.uid : null,
  );
  return (
    <AuthGate state={state} signIn={signIn}>
      {state.status === 'ready' && (
        <App
          key={state.session.uid}
          account={<AccountMenu profile={profile} signOut={signOut} />}
        />
      )}
    </AuthGate>
  );
}
