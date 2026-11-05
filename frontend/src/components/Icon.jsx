// Small stroke icons (24x24 grid), drawn inline so the app needs no icon dependency.
// Icons are decorative: they are hidden from assistive technology and always sit next to a text label.
const PATHS = {
  dashboard: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z',
  files: 'M4 4h6l2 2h8v13H4z',
  architecture: 'M12 3v4M12 7H5v4M12 7h7v4M5 11v3M19 11v3M3 14h4v4H3zM17 14h4v4h-4zM10 14h4v4h-4zM12 11v3',
  database: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  tradeoffs: 'M12 3v18M5 7h14M7 7l-3 7a3 3 0 0 0 6 0zM17 7l-3 7a3 3 0 0 0 6 0zM8 21h8',
  reliability: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM8.5 12l2.5 2.5L16 9.5',
  performance: 'M4 20V10M10 20V4M16 20v-8M22 20H2',
  simulation: 'M5 3h14v18H5zM8 7h8M8 11h2M12 11h2M16 11h0M8 15h2M12 15h2M16 15h0M8 18h8',
  docs: 'M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7',
  upload: 'M12 16V4M7 9l5-5 5 5M4 16v4h16v-4',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5',
  play: 'M7 4l13 8-13 8z',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  menu: 'M4 6h16M4 12h16M4 18h16',
  chevrons: 'M7 6l-5 6 5 6M17 6l5 6-5 6',
  logo: 'M4 8l8-4 8 4-8 4zM4 12l8 4 8-4M4 16l8 4 8-4',
}

export default function Icon({ name, className = 'nav-icon', size }) {
  const d = PATHS[name]
  if (!d) return null
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={d} />
    </svg>
  )
}
