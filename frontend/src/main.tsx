import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MotionConfig } from 'framer-motion';
import { AuthenticatedApp } from './app/AuthenticatedApp';
import './styles/index.css';

const root = document.getElementById('root');
if (!root) throw new Error('Root element is missing');

createRoot(root).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <AuthenticatedApp />
    </MotionConfig>
  </StrictMode>,
);
