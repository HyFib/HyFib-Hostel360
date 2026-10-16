<?php
/**
 * Menu entries for the official hostel website, as JSON. The website can render its
 * "Hostel portal" menu from this list so the links always match the live portal.
 * Cross-origin reads are allowed only for the origins in H360_WEBSITE_ORIGINS.
 */

declare(strict_types=1);

$config = require __DIR__ . '/../src/config.php';
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$origin = rtrim($_SERVER['HTTP_ORIGIN'] ?? '', '/');

header('Vary: Origin');
if ($origin !== '' && in_array($origin, $config['website_origins'], true)) {
	header('Access-Control-Allow-Origin: ' . $origin);
	header('Access-Control-Allow-Methods: GET, HEAD, OPTIONS');
	header('Access-Control-Max-Age: 86400');
}

if ($method === 'OPTIONS') {
	http_response_code(204);
	exit;
}
if ($method !== 'GET' && $method !== 'HEAD') {
	header('Allow: GET, HEAD, OPTIONS');
	http_response_code(405);
	exit;
}

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: public, max-age=300');

echo json_encode(
	[
		'institution' => $config['institution_name'],
		'portal_url' => $config['portal_url'],
		'links' => array_map(
			static fn (array $link): array => [
				'key' => $link['key'],
				'label' => $link['label'],
				'label_ta' => $link['label_ta'],
				'url' => $link['url'],
			],
			$config['links']
		),
		'support' => ['phone' => $config['support_phone'], 'email' => $config['support_email']],
	],
	JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR
);
