<?php
require_once __DIR__ . '/../../src/bootstrap.php';

$input = request_json();
$lat = (float)($input['lat'] ?? 0);
$lon = (float)($input['lon'] ?? 0);

$gis = new GisService();

json_response([
    'ok' => true,
    'nearbyRoads' => $gis->getNearbyRoads($lat, $lon, 3),
    'nearbyPlaces' => $gis->getNearbyPlaces($lat, $lon, 3),
]);