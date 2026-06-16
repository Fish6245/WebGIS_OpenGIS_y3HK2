<?php

require_once __DIR__ . '/../../src/bootstrap.php';

$service =
    new EstimateService();

json_response(
    $service->predictPrice(
        request_json()
    )
);