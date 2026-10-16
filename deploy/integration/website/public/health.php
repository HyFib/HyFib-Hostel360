<?php
/** Health endpoint for the integration pages: PHP-FPM is up and executing PHP. */

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
echo json_encode(['status' => 'ok'], JSON_THROW_ON_ERROR);
