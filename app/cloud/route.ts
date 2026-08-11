/** `/cloud` — the neti cloud page, including the contact modal that posts to `/api/contact`. */
import { page } from '../route'

export const dynamic = 'force-static'

export function GET() {
  return page('cloud', 'index.html')
}
