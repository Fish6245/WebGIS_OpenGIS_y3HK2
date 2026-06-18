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
        'nearbyStatus' => 'pending',
    ]);
}

$address = trim(
    implode(', ', array_filter([
        $row['TenDuong'] ?? '',
        $row['Phuong'] ?? '',
        $row['QuanHuyen'] ?? '',
        $row['TinhThanh'] ?? '',
    ]))
);

json_response([
    'ok' => true,
    'address' => $address !== '' ? $address : ($row['Address_found'] ?? 'Chưa xác định'),
    'feature' => [
        'TenDuong' => $row['TenDuong'] ?? null,

        'Phuong' => $row['Phuong'] ?? null,
        'PhuongRaw' => $row['PhuongRaw'] ?? null,
        'PhuongLoai' => $row['PhuongLoai'] ?? null,

        'QuanHuyen' => $row['QuanHuyen'] ?? null,
        'QuanHuyenRaw' => $row['QuanHuyenRaw'] ?? null,
        'QuanHuyenLoai' => $row['QuanHuyenLoai'] ?? null,

        'TinhThanh' => $row['TinhThanh'] ?? null,
        'Address_found' => $row['Address_found'] ?? null,
    ],
    'lat' => $lat,
    'lon' => $lon
]);