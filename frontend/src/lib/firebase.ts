import { deleteApp, initializeApp } from 'firebase/app';
import { runAuthOperation } from './authLifecycle';
import {
  browserLocalPersistence,
  browserPopupRedirectResolver,
  GoogleAuthProvider,
  indexedDBLocalPersistence,
  inMemoryPersistence,
  initializeAuth,
  signInWithPopup,
  signOut,
} from 'firebase/auth';

// Public web identifiers only. Admin credentials must never enter the frontend.
const app = initializeApp({
  apiKey: 'AIzaSyD4YW8krEjQXPc-0Q10B48BvXAr5WSW6CU',
  authDomain: 'alex-xu-central.firebaseapp.com',
  projectId: 'alex-xu-central',
  storageBucket: 'alex-xu-central.firebasestorage.app',
  messagingSenderId: '267612792066',
  appId: '1:267612792066:web:8ac84f7470cb6657108e1b',
});
// The Google sign-in survives reloads so hourly renewal keeps working; the
// server still ends every session 12 h after the Google sign-in (`auth_time`).
const auth = initializeAuth(app, {
  persistence: [
    indexedDBLocalPersistence,
    browserLocalPersistence,
    inMemoryPersistence,
  ],
  popupRedirectResolver: browserPopupRedirectResolver,
});
const provider = new GoogleAuthProvider();
provider.setCustomParameters({ prompt: 'select_account' });

export async function googleToken() {
  const result = await signInWithPopup(auth, provider);
  return result.user.getIdToken();
}
export async function renewedToken(uid: string) {
  await auth.authStateReady();
  if (auth.currentUser?.uid !== uid) throw new Error('Sign in again.');
  return auth.currentUser.getIdToken(true);
}
export function signOutGoogle() {
  return signOut(auth);
}

// Dedicated ephemeral instance: works after reload with only the app cookie,
// and never replaces the main SDK user or app session. Prepare before click two.
export function createGoogleStepUp(uid: string) {
  const isolatedApp = initializeApp(
    app.options,
    `ww-step-up-${crypto.randomUUID()}`,
  );
  const isolatedAuth = initializeAuth(isolatedApp, {
    persistence: inMemoryPersistence,
    popupRedirectResolver: browserPopupRedirectResolver,
  });
  const google = new GoogleAuthProvider();
  google.setCustomParameters({ prompt: 'select_account' });
  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    void runAuthOperation((operation) => operation.wait(signOut(isolatedAuth)))
      .catch(() => {})
      .finally(() => {
        void deleteApp(isolatedApp).catch(() => {});
      });
  };
  return {
    ready: () => isolatedAuth.authStateReady(),
    dispose,
    verify: () => {
      if (disposed) return Promise.reject(new Error('Verification cancelled.'));
      // Invoke popup directly in the second user gesture, before any awaited work.
      return signInWithPopup(isolatedAuth, google)
        .then(async (result) => {
          if (disposed || result.user.uid !== uid)
            throw new Error('Verify the same administrator account.');
          const token = await runAuthOperation((operation) =>
            operation.wait(result.user.getIdToken()),
          );
          if (disposed) throw new Error('Verification cancelled.');
          return token;
        })
        .finally(dispose);
    },
  };
}
