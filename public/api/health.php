<?php
require_once __DIR__ . '/../../src/bootstrap.php';

json_response([
    'ok' => true,
    'message' => 'Web GIS PHP is running',
]);