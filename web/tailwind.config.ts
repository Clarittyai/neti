import type { Config } from "tailwindcss"

const config = {
  darkMode: ["class"],
  // Gate every `hover:` utility behind `@media (hover: hover)` so hover styles
  // never apply — and never "stick" after a tap — on touch devices (mobile + PWA).
  future: { hoverOnlyWhenSupported: true },
  content: ['./src/**/*.{ts,tsx}'],
  prefix: "",
  theme: {
    screens: {
      'sm': '640px',
      'md': '920px',
      'lg': '1024px',
      'xl': '1280px',
      '2xl': '1536px',
    },
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
          50: '#F9FAFB',
          100: '#F3F4F6',
          200: '#E5E7EB',
          300: '#D1D5DB',
          400: '#9CA3AF',
          500: '#6B7280',
          600: '#4B5563',
          700: '#374151',
          800: '#1F2937',
          900: '#111827',
          950: '#000000',
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
          // Tailwind's violet scale: lightness-monotone, hue-stable (2 degrees of spread), and
          // anchored so 500 IS --accent. A hand-mixed ramp drifts in hue and the hover step stops
          // looking like the same colour.
          50: '#F5F3FF',
          100: '#EDE9FE',
          200: '#DDD6FE',
          300: '#C4B5FD',
          400: '#A78BFA',
          500: '#8B5CF6',
          600: '#7C3AED',
          700: '#6D28D9',
          800: '#5B21B6',
          900: '#4C1D95',
        },
        verdict: {
          block: 'hsl(var(--verdict-block))',
          confirm: 'hsl(var(--verdict-confirm))',
          allow: 'hsl(var(--verdict-allow))',
          unknown: 'hsl(var(--verdict-unknown))',
        },
        // Vibrant brand colors from website
        orange: {
          DEFAULT: '#FF9500',
          50: '#FFF7ED',
          100: '#FFEDD5',
          200: '#FED7AA',
          300: '#FDBA74',
          400: '#FB923C',
          500: '#FF9500',
          600: '#EA580C',
          700: '#C2410C',
          800: '#9A3412',
          900: '#7C2D12',
        },
        purple: {
          DEFAULT: '#AF52DE',
          50: '#FAF5FF',
          100: '#F3E8FF',
          200: '#E9D5FF',
          300: '#D8B4FE',
          400: '#C084FC',
          500: '#AF52DE',
          600: '#9333EA',
          700: '#7E22CE',
          800: '#6B21A8',
          900: '#581C87',
        },
        green: {
          DEFAULT: '#34C759',
          50: '#F0FDF4',
          100: '#DCFCE7',
          200: '#BBF7D0',
          300: '#86EFAC',
          400: '#4ADE80',
          500: '#34C759',
          600: '#16A34A',
          700: '#15803D',
          800: '#166534',
          900: '#14532D',
        },
        pink: {
          DEFAULT: '#FF69B4',
          50: '#FFF5F7',
          100: '#FFE4EC',
          200: '#FFC9DE',
          300: '#FFA3CA',
          400: '#FF7BB8',
          500: '#FF69B4',
          600: '#FF1493',
          700: '#E6007A',
          800: '#B8005F',
          900: '#8A0047',
        },
        teal: {
          DEFAULT: '#5AC8FA',
          50: '#F0FDFA',
          100: '#CCFBF1',
          200: '#99F6E4',
          300: '#5EEAD4',
          400: '#2DD4BF',
          500: '#5AC8FA',
          600: '#0D9488',
          700: '#0F766E',
          800: '#115E59',
          900: '#134E4A',
        },
        yellow: {
          DEFAULT: '#FFD60A',
          50: '#FFFBEB',
          100: '#FFF3C4',
          200: '#FFE58F',
          300: '#FFD60A',
          400: '#FFC107',
          500: '#FFB300',
          600: '#FFA000',
          700: '#FF8F00',
          800: '#FF6F00',
          900: '#E65100',
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        success: {
          DEFAULT: 'hsl(var(--success))',
          foreground: 'hsl(var(--success-foreground))',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          foreground: 'hsl(var(--warning-foreground))',
        },
        info: {
          DEFAULT: 'hsl(var(--info))',
          foreground: 'hsl(var(--info-foreground))',
        },
        'surface-deep': {
          DEFAULT: 'hsl(var(--surface-deep))',
          foreground: 'hsl(var(--surface-deep-foreground))',
        },
      },
      fontFamily: {
        // `--font-sans` = Inter, loaded by next/font in layout.tsx. The ONE
        // family in the product — body AND headings (DESIGN_PRINCIPLES); the
        // rest are fallbacks until the webfont lands / on the rare host
        // without it. Weight, size and tracking carry hierarchy, not a second
        // typeface.
        sans: ['var(--font-sans)', '-apple-system', 'BlinkMacSystemFont', 'SF Pro Display', 'Inter', 'system-ui', 'sans-serif'],
        // `display` is an ALIAS of `sans`, kept so a stray `font-display` (or a
        // generated app carrying the old class) renders Inter instead of
        // falling back to a random system face. Don't reach for it in new UI.
        display: ['var(--font-sans)', '-apple-system', 'BlinkMacSystemFont', 'SF Pro Display', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['SF Mono', 'Monaco', 'Cascadia Code', 'Roboto Mono', 'monospace'],
      },
      // Semantic z-index hierarchy. Use these names instead of arbitrary numbers so the
      // stack is consistent: sheet > sidebar > nav > page content. Mobile bottom sheets
      // use `z-sheet` so they always paint above the bottom nav (`z-nav`).
      zIndex: {
        // Layered overlay system. Full-screen `overlay` containers
        // (AppDialog, ImageLightbox) sit BELOW the popup/menu/sheet layers
        // so anything opened from inside an overlay (tooltip on hover,
        // kebab → OptionsMenu, ConfirmationDialog, etc.) renders on top
        // of the overlay, not hidden behind it.
        //
        // Stack: nav < sidebar < overlay < popover < dropdown < sheet-backdrop < sheet
        'nav': '50',
        'sidebar': '55',
        'overlay': '60',
        'popover': '70',
        'dropdown': '80',
        'sheet-backdrop': '90',
        'sheet': '100',
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'gradient-conic': 'conic-gradient(from 180deg at 50% 50%, var(--tw-gradient-stops))',
        'gradient-mesh': 'radial-gradient(at 40% 20%, hsla(240,100%,70%,0.15) 0px, transparent 50%), radial-gradient(at 80% 0%, hsla(190,100%,75%,0.15) 0px, transparent 50%), radial-gradient(at 0% 50%, hsla(330,100%,75%,0.12) 0px, transparent 50%), radial-gradient(at 100% 100%, hsla(30,100%,65%,0.1) 0px, transparent 50%)',
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        'xl': '1rem',
        '2xl': '1.5rem',
        '3xl': '2rem',
      },
      boxShadow: {
        'glass': '0 8px 32px 0 rgba(31, 38, 135, 0.1)',
        'lift': '0 10px 40px -10px rgba(0, 0, 0, 0.1)',
        'lift-lg': '0 20px 60px -15px rgba(0, 0, 0, 0.15)',
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        float: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%': { transform: 'translateY(-20px)' },
        },
        glow: {
          '0%': { opacity: '0.5', filter: 'blur(20px)' },
          '100%': { opacity: '1', filter: 'blur(30px)' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        scaleIn: {
          '0%': { transform: 'scale(0.95)', opacity: '0' },
          '100%': { transform: 'scale(1)', opacity: '1' },
        },
        // Toasts fade in/out through a soft blur ("smoke") — no slide.
        'toast-blur-in': {
          '0%': { opacity: '0', filter: 'blur(14px)', transform: 'scale(0.97)' },
          '100%': { opacity: '1', filter: 'blur(0px)', transform: 'scale(1)' },
        },
        'toast-blur-out': {
          '0%': { opacity: '1', filter: 'blur(0px)', transform: 'scale(1)' },
          '100%': { opacity: '0', filter: 'blur(14px)', transform: 'scale(0.97)' },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        'float': 'float 6s ease-in-out infinite',
        'glow': 'glow 2s ease-in-out infinite alternate',
        'fade-in': 'fadeIn 0.5s ease-out',
        'scale-in': 'scaleIn 0.5s ease-out',
        'toast-blur-in': 'toast-blur-in 0.32s cubic-bezier(0.22, 1, 0.36, 1)',
        'toast-blur-out': 'toast-blur-out 0.26s ease-in forwards',
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
} satisfies Config

export default config
