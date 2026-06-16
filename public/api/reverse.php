<?php
require_once __DIR__ . '/../../src/bootstrap.php';

$input = request_json();
$lat = (float)($input['lat'] ?? 0);
$lon = (float)($input['lon'] ?? 0);

$gis = new GisService();
$row = $gis->findNearestRoadFeature($lat, $lon);

if (!$row) {
    json_response([
        'ok' => true,
        'address' => 'Chưa xác định',
        'lat' => $lat,
        'lon' => $lon,
        'nearbyStatus' => 'pending', // BỔ SUNG
    ]);
}

$address = trim(
    ($row['Address_found'] ?? $row['TenDuong'] ?? 'Chưa xác định')
    . ', '
    . ($row['QuanHuyen'] ?? '')
);

json_response([
    'ok'=>true,
    'address'=>$address,

    'feature'=>[
        'TenDuong'=>$row['TenDuong'] ?? null,
        'Phuong'=>$row['Phuong'] ?? null,
        'QuanHuyen'=>$row['QuanHuyen'] ?? null,
        'Address_found'=>$row['Address_found'] ?? null,
    ],

    'lat'=>$lat,
    'lon'=>$lon
]);