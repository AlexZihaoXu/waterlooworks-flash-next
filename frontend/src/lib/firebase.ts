import { initializeApp } from 'firebase/app';
import {
  browserPopupRedirectResolver,
  GoogleAuthProvider,
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
const auth = initializeAuth(app, {
  persistence: inMemoryPersistence,
  popupRedirectResolver: browserPopupRedirectResolver,
});
const provider = new GoogleAuthProvider();
provider.setCustomParameters({ prompt: 'select_account' });

export async function googleToken() {
  const result = await signInWithPopup(auth, provider);
  return result.user.getIdToken();
}
export async function renewedToken(uid: string) {
  if (auth.currentUser?.uid !== uid) throw new Error('Sign in again.');
  return auth.currentUser.getIdToken(true);
}
export function signOutGoogle() {
  return signOut(auth);
}
