<?php
/**
 * Hostel360 entry page for the official hostel website: the website's menu entries point
 * here (or straight at the links below), so students, parents and staff reach the portal
 * from the institution's own site.
 */

declare(strict_types=1);

$config = require __DIR__ . '/../src/config.php';

$e = static fn (string $value): string => htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE | ENT_HTML5, 'UTF-8');

header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: public, max-age=300');
?>
<!doctype html>
<html lang="en">
<head>
	<meta charset="utf-8">
	<meta name="viewport" content="width=device-width, initial-scale=1">
	<meta name="description" content="<?= $e($config['institution_name']) ?> hostel portal: fees, mess, leave, grievances and certificates.">
	<title><?= $e($config['institution_short_name']) ?> Hostels &middot; Hostel360</title>
	<link rel="stylesheet" href="assets/site.css">
</head>
<body>
	<header class="masthead">
		<div class="wrap">
			<p class="eyebrow">Hostel360</p>
			<h1><?= $e($config['institution_name']) ?></h1>
			<p class="lead">Hostel services online &middot; <span lang="ta">விடுதி சேவைகள் இணையத்தில்</span></p>
		</div>
	</header>

	<main class="wrap">
		<ul class="links">
<?php foreach ($config['links'] as $link): ?>
			<li>
				<a class="card" href="<?= $e($link['url']) ?>">
					<span class="card-title"><?= $e($link['label']) ?></span>
					<span class="card-title-ta" lang="ta"><?= $e($link['label_ta']) ?></span>
					<span class="card-text"><?= $e($link['description']) ?></span>
				</a>
			</li>
<?php endforeach; ?>
		</ul>

		<section class="help" aria-labelledby="help-title">
			<h2 id="help-title">Need help? <span lang="ta">உதவி தேவையா?</span></h2>
			<p>
				Hostel360 helpdesk, 24&times;7:
				<a href="tel:<?= $e(preg_replace('/[^0-9+]/', '', $config['support_phone'])) ?>"><?= $e($config['support_phone']) ?></a>
				&middot;
				<a href="mailto:<?= $e($config['support_email']) ?>"><?= $e($config['support_email']) ?></a>
			</p>
		</section>
	</main>

	<footer class="wrap footer">
		<p>Hostel360 by HyFib Solutions Private Limited</p>
	</footer>
</body>
</html>
