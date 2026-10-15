<?php
/**
 * HyFib Hostel360: sign-in buttons for PHP-based institution websites.
 *
 * Usage (anywhere in a PHP template):
 *
 *     require_once __DIR__ . '/hostel360-links.php';
 *     echo hostel360_links('https://hostel.accet.ac.in');
 *     echo hostel360_links('https://hostel.accet.ac.in', ['lang' => 'ta', 'layout' => 'column']);
 *
 * Options:
 *     lang      'both' (default: English with Tamil underneath), 'en' or 'ta'
 *     layout    'row' (default, wraps on small screens) or 'column'
 *     show      list of 'student', 'parent', 'staff' (default: all three)
 *     target    '_self' (default) or '_blank'
 *     styles    true (default) to include the small stylesheet once per page; false to style
 *               the .h360-* classes from the site's own CSS
 *
 * The markup is plain HTML with no script, cookies or tracking. Every value written to the
 * page is escaped. Works on PHP 7.2 and later.
 */

if (!function_exists('hostel360_links')) {
	/**
	 * Return the HTML for the portal sign-in buttons, or an empty string when the portal
	 * address is not a valid https URL.
	 *
	 * @param string $portal  Base address of the portal, e.g. https://hostel.accet.ac.in
	 * @param array  $options See the file header.
	 */
	function hostel360_links($portal, array $options = array())
	{
		static $styled = false;

		$origin = hostel360_portal_origin($portal);
		if ($origin === null) {
			trigger_error('hostel360_links(): the portal address must be an https:// URL.', E_USER_WARNING);
			return '';
		}

		$lang = hostel360_pick(isset($options['lang']) ? $options['lang'] : null, array('both', 'en', 'ta'), 'both');
		$layout = hostel360_pick(isset($options['layout']) ? $options['layout'] : null, array('row', 'column'), 'row');
		$target = hostel360_pick(isset($options['target']) ? $options['target'] : null, array('_self', '_blank'), '_self');
		$show = isset($options['show']) && is_array($options['show']) ? $options['show'] : array('student', 'parent', 'staff');
		$with_styles = !isset($options['styles']) || $options['styles'];

		$buttons = array(
			'student' => array('/hostel/login?as=student', 'Student login', 'மாணவர் உள்நுழைவு'),
			'parent' => array('/hostel/login?as=parent', 'Parent login', 'பெற்றோர் உள்நுழைவு'),
			'staff' => array('/login', 'Staff login', 'பணியாளர் உள்நுழைவு'),
		);

		$html = '';
		if ($with_styles && !$styled) {
			$html .= '<style>' . hostel360_links_css() . '</style>';
			$styled = true;
		}

		$nav_label = $lang === 'ta' ? 'விடுதி இணையதளம்' : 'Hostel portal';
		$html .= '<nav class="h360-nav" aria-label="' . hostel360_e($nav_label) . '">';
		$html .= '<ul class="h360' . ($layout === 'column' ? ' h360-column' : '') . '">';
		foreach ($buttons as $kind => $button) {
			if (!in_array($kind, $show, true)) {
				continue;
			}
			list($path, $english, $tamil) = $button;
			$attributes = ' class="h360-btn h360-' . hostel360_e($kind) . '" href="' . hostel360_e($origin . $path) . '"';
			if ($target === '_blank') {
				$attributes .= ' target="_blank" rel="noopener noreferrer"';
			}
			$html .= '<li><a' . $attributes . '><span class="h360-text">';
			if ($lang === 'ta') {
				$html .= '<span class="h360-main" lang="ta">' . hostel360_e($tamil) . '</span>';
			} else {
				$html .= '<span class="h360-main"' . ($lang === 'both' ? ' lang="en"' : '') . '>' . hostel360_e($english) . '</span>';
				if ($lang === 'both') {
					$html .= '<span class="h360-sub" lang="ta">' . hostel360_e($tamil) . '</span>';
				}
			}
			$html .= '</span></a></li>';
		}
		$html .= '</ul></nav>';
		return $html;
	}

	/** Scheme and host of an https URL (http only for localhost testing), or null. */
	function hostel360_portal_origin($portal)
	{
		if (!is_string($portal) || filter_var($portal, FILTER_VALIDATE_URL) === false) {
			return null;
		}
		$parts = parse_url($portal);
		if (!isset($parts['scheme'], $parts['host'])) {
			return null;
		}
		$scheme = strtolower($parts['scheme']);
		$host = strtolower($parts['host']);
		$local = in_array($host, array('localhost', '127.0.0.1', '[::1]'), true);
		if ($scheme !== 'https' && !($scheme === 'http' && $local)) {
			return null;
		}
		if (!preg_match('/^[a-z0-9.\-\[\]:]+$/', $host)) {
			return null;
		}
		$port = isset($parts['port']) ? ':' . (int) $parts['port'] : '';
		return $scheme . '://' . $host . $port;
	}

	function hostel360_pick($value, array $allowed, $fallback)
	{
		$value = is_string($value) ? strtolower($value) : '';
		return in_array($value, $allowed, true) ? $value : $fallback;
	}

	function hostel360_e($value)
	{
		return htmlspecialchars((string) $value, ENT_QUOTES | ENT_HTML5, 'UTF-8');
	}

	function hostel360_links_css()
	{
		return '.h360{display:flex;flex-wrap:wrap;gap:.75rem;margin:0;padding:0;list-style:none;'
			. 'font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans","Noto Sans Tamil","Nirmala UI",Latha,Arial,sans-serif}'
			. '.h360.h360-column{flex-direction:column}'
			. '.h360 li{margin:0;padding:0;flex:1 1 12rem;min-width:0;list-style:none}'
			. '.h360.h360-column li{flex:none}'
			. '.h360 .h360-btn{box-sizing:border-box;display:flex;align-items:center;min-height:3rem;padding:.65rem 1rem;'
			. 'border-radius:.75rem;border:2px solid #0b4f9c;background:#0b4f9c;color:#fff;text-decoration:none;'
			. 'font-weight:600;font-size:1rem;line-height:1.25}'
			. '.h360 .h360-btn:hover{background:#083b75;border-color:#083b75;color:#fff}'
			. '.h360 .h360-btn:focus-visible{outline:3px solid #f59e0b;outline-offset:2px}'
			. '.h360 .h360-staff{background:#fff;color:#0b4f9c}'
			. '.h360 .h360-staff:hover{background:#e7f0fb;color:#0b4f9c;border-color:#0b4f9c}'
			. '.h360 .h360-text{display:flex;flex-direction:column}'
			. '.h360 .h360-sub{font-weight:500;font-size:.85rem;opacity:.9}';
	}
}
