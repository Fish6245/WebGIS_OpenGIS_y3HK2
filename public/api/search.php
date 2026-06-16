<?php
require_once __DIR__ . '/../../src/bootstrap.php';

$input = request_json();
$keyword = trim((string)($input['keyword'] ?? ''));

$service = new EstimateService();
json_response([
    'ok' => true,
    'items' => $service-> searchAddress($keyword),
]);