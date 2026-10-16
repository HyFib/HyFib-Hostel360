<?php
/**
 * Settings for the hostel website integration pages, read from the PHP-FPM pool
 * environment (see deploy/php-fpm/pool.d/www.conf and deploy/compose/.env.example).
 */

declare(strict_types=1);

$env = static function (string $name, string $default = ''): string {
	$value = getenv($name);
	return ($value === false || trim($value) === '') ? $default : trim($value);
};

$portal = rtrim($env('H360_PORTAL_URL', 'https://hostel.example.ac.in'), '/');
if (!preg_match('#^https://[A-Za-z0-9.-]+(:[0-9]+)?$#', $portal)) {
	// A malformed setting must not turn into a broken or unsafe link on the page.
	$portal = 'https://' . preg_replace('#[^A-Za-z0-9.-]#', '', parse_url($portal, PHP_URL_HOST) ?: 'localhost');
}

$origins = array_values(array_filter(array_map(
	static fn (string $origin): string => rtrim(trim($origin), '/'),
	explode(',', $env('H360_WEBSITE_ORIGINS'))
), static fn (string $origin): bool => (bool) preg_match('#^https://[A-Za-z0-9.-]+(:[0-9]+)?$#', $origin)));

return [
	'portal_url' => $portal,
	'institution_name' => $env('H360_INSTITUTION_NAME', 'Hostel Office'),
	'institution_short_name' => $env('H360_INSTITUTION_SHORT_NAME', 'Hostel'),
	'support_phone' => $env('H360_SUPPORT_PHONE', '+91 96060 60596'),
	'support_email' => $env('H360_SUPPORT_EMAIL', 'support@hyfib.com'),
	'website_origins' => $origins,
	'links' => [
		[
			'key' => 'portal',
			'label' => 'Student and parent login',
			'label_ta' => 'மாணவர் மற்றும் பெற்றோர் உள்நுழைவு',
			'description' => 'Fees, mess menu, leave, grievances and certificates. Sign in with an OTP sent to your registered mobile.',
			'url' => $portal . '/hostel/login',
		],
		[
			'key' => 'fees',
			'label' => 'Pay hostel fees',
			'label_ta' => 'விடுதிக் கட்டணம் செலுத்த',
			'description' => 'See your dues and pay online. Receipts can be downloaded at any time.',
			'url' => $portal . '/hostel/fees',
		],
		[
			'key' => 'staff',
			'label' => 'Staff login',
			'label_ta' => 'பணியாளர் உள்நுழைவு',
			'description' => 'Wardens, hostel office and mess staff.',
			'url' => $portal . '/login',
		],
		[
			'key' => 'verify',
			'label' => 'Verify a bonafide certificate',
			'label_ta' => 'சான்றிதழைச் சரிபார்க்க',
			'description' => 'Check that a hostel bonafide certificate is genuine.',
			'url' => $portal . '/verify-certificate',
		],
	],
];
