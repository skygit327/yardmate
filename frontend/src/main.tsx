import 'leaflet/dist/leaflet.css';
import React from 'react';
import ReactDOM from 'react-dom/client';
import AuthGate from './AuthGate';
import { LanguageProvider } from './language';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><LanguageProvider><AuthGate /></LanguageProvider></React.StrictMode>,
);
