import { useEffect, useRef, useState } from 'react';
import { useLanguage } from './language';

// City-level positions only: the companies in the demo are synthetic, so pins never point at a
// real shipyard. Regions are the province names the notices use (Gyeongnam, Ulsan, Busan),
// escaped to keep the source ASCII.
export const yardPlaces = [
  { name: 'Geoje', lat: 34.8806, lng: 128.6211, kind: 'home', note: 'Our plant · hull customer', region: '\uACBD\uB0A8' },
  { name: 'Ulsan', lat: 35.5384, lng: 129.3114, kind: 'yard', note: 'Coating customer', region: '\uC6B8\uC0B0', side: 'left' },
  { name: 'Ulsan Dong-gu', lat: 35.5049, lng: 129.4166, kind: 'crisis', region: '\uC6B8\uC0B0' },
  { name: 'Changwon Jinhae', lat: 35.1328, lng: 128.7102, kind: 'crisis', region: '\uACBD\uB0A8' },
  { name: 'Goseong', lat: 34.9730, lng: 128.3223, kind: 'crisis', region: '\uACBD\uB0A8' },
  { name: 'Tongyeong', lat: 34.8544, lng: 128.4331, kind: 'crisis', note: 'Pipe shop', region: '\uACBD\uB0A8' },
  { name: 'Busan port', lat: 35.1040, lng: 129.0420, kind: 'port', region: '\uBD80\uC0B0' },
] as const;

function escapeHtml(text: string) {
  return text.replace(/[&<>"']/g, char => `&#${char.charCodeAt(0)};`);
}

/** OpenStreetMap through Leaflet: no key, no sign-up. Falls back when tiles cannot load. */
export default function LiveMap({ onRegion, onFail }: { onRegion: (region: string) => void; onFail: () => void }) {
  const { t } = useLanguage();
  const element = useRef<HTMLDivElement>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let map: import('leaflet').Map | null = null;
    let offline = 0;
    let cancelled = false;
    // Leaflet touches window on import, so it is loaded only in the browser.
    import('leaflet').then(({ default: L }) => {
      if (cancelled || !element.current) return;
      map = L.map(element.current, { zoomControl: false, scrollWheelZoom: false, attributionControl: true })
        .setView([35.17, 128.86], 9);
      L.control.zoom({ position: 'topright' }).addTo(map);
      map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>');
      let tiles = 0, errors = 0;
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 15, minZoom: 7,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>',
      })
        .on('tileload', () => { tiles += 1; setLoaded(true); })
        .on('tileerror', () => { errors += 1; if (!tiles && errors >= 4) onFail(); })
        .addTo(map);
      for (const place of yardPlaces) {
        if (place.kind === 'crisis' || place.kind === 'home') {
          L.circle([place.lat, place.lng], {
            radius: 6000, color: '#b4482b', weight: 2, dashArray: '5 5', fillColor: '#b4482b', fillOpacity: 0.08, interactive: false,
          }).addTo(map);
        }
        const note = 'note' in place ? `<small>${escapeHtml(t(place.note))}</small>` : '';
        L.marker([place.lat, place.lng], {
          title: `${t(place.name)} · ${t('Show notices for this region')}`,
          keyboard: true,
          icon: L.divIcon({
            className: '',
            html: `<span class="map-pin ${place.kind}${'side' in place ? ' left' : ''}"><i></i><span class="map-pin-label">${escapeHtml(t(place.name))}${note}</span></span>`,
            iconAnchor: [8, 8],
          }),
        }).on('click keypress', () => onRegion(place.region)).addTo(map);
      }
      offline = window.setTimeout(() => { if (!tiles) onFail(); }, 10000);
    }).catch(() => { if (!cancelled) onFail(); });
    return () => { cancelled = true; window.clearTimeout(offline); map?.remove(); };
  }, [t]);

  return <div className="live-map" role="region" aria-label={t("Shipbuilding suppliers on the Busan–Ulsan–Gyeongnam coast")}>
    <div className="live-map-canvas" ref={element} />
    {!loaded && <span className="live-map-loading">{t("Loading the map…")}</span>}
  </div>;
}
