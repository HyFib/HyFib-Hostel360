<?php
/**
 * Container health check for PHP-FPM: sends a FastCGI request for the pool's ping path and
 * expects "pong", which proves the master accepts connections and can start a worker.
 *
 * Run with "php -n" so the hardened runtime ini (meant for the web pool) does not apply.
 * Exit status: 0 healthy, 1 unhealthy.
 */

declare(strict_types=1);

const FCGI_VERSION = 1;
const FCGI_BEGIN_REQUEST = 1;
const FCGI_PARAMS = 4;
const FCGI_STDIN = 5;
const FCGI_STDOUT = 6;
const FCGI_END_REQUEST = 3;
const FCGI_RESPONDER = 1;
const REQUEST_ID = 1;

function record(int $type, string $content): string
{
	return pack('CCnnCx', FCGI_VERSION, $type, REQUEST_ID, strlen($content), 0) . $content;
}

function pair(string $name, string $value): string
{
	$encode = static fn (int $length): string => $length < 128 ? chr($length) : pack('N', $length | 0x80000000);
	return $encode(strlen($name)) . $encode(strlen($value)) . $name . $value;
}

$params = '';
foreach ([
	'GATEWAY_INTERFACE' => 'FastCGI/1.0',
	'REQUEST_METHOD' => 'GET',
	'SCRIPT_NAME' => '/fpm-ping',
	'SCRIPT_FILENAME' => '/fpm-ping',
	'REQUEST_URI' => '/fpm-ping',
	'QUERY_STRING' => '',
	'SERVER_SOFTWARE' => 'hostel360-healthcheck',
	'REMOTE_ADDR' => '127.0.0.1',
	'SERVER_PROTOCOL' => 'HTTP/1.1',
] as $name => $value) {
	$params .= pair($name, $value);
}

$socket = @stream_socket_client('tcp://127.0.0.1:9000', $errno, $error, 3.0);
if ($socket === false) {
	fwrite(STDERR, "php-fpm is not accepting connections: {$error}\n");
	exit(1);
}
stream_set_timeout($socket, 5);

fwrite($socket, record(FCGI_BEGIN_REQUEST, pack('nCx5', FCGI_RESPONDER, 0)));
fwrite($socket, record(FCGI_PARAMS, $params));
fwrite($socket, record(FCGI_PARAMS, ''));
fwrite($socket, record(FCGI_STDIN, ''));

$output = '';
while (!feof($socket)) {
	$header = fread($socket, 8);
	if ($header === false || strlen($header) < 8) {
		break;
	}
	$record = unpack('Cversion/Ctype/nrequest/nlength/Cpadding/Creserved', $header);
	$body = $record['length'] > 0 ? stream_get_contents($socket, $record['length']) : '';
	if ($record['padding'] > 0) {
		fread($socket, $record['padding']);
	}
	if ($record['type'] === FCGI_STDOUT) {
		$output .= $body;
	} elseif ($record['type'] === FCGI_END_REQUEST) {
		break;
	}
}
fclose($socket);

if (!str_contains($output, 'pong')) {
	fwrite(STDERR, "unexpected ping response\n");
	exit(1);
}
exit(0);
