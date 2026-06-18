<?php
declare(strict_types=1);
// GisService.php

class GisService
{
    private ?PDO $db;

    public function __construct()
    {
        $this->db = Database::conn();
    }

    // =========================
    // BỔ SUNG: tính khoảng cách
    // =========================
    private function haversineMeters(float $lat1, float $lon1, float $lat2, float $lon2): float
    {
        $earthRadius = 6371000.0;

        $dLat = deg2rad($lat2 - $lat1);
        $dLon = deg2rad($lon2 - $lon1);

        $a = sin($dLat / 2) ** 2
            + cos(deg2rad($lat1)) * cos(deg2rad($lat2)) * sin($dLon / 2) ** 2;

        return 2 * $earthRadius * asin(min(1, sqrt($a)));
    }

    // =========================
    // BỔ SUNG: kiểm tra bảng tồn tại
    // =========================
    private function tableExists(string $tableName): bool
    {
        if (!$this->db) {
            return false;
        }

        try {
            $sql = "
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :table
                LIMIT 1
            ";
            $stmt = $this->db->prepare($sql);
            $stmt->execute([':table' => $tableName]);
            return (bool)$stmt->fetchColumn();
        } catch (Throwable $e) {
            return false;
        }
    }

    // =========================
    // BỔ SUNG: gọi Overpass API
    // =========================
    private function overpassRequest(string $query): ?array
    {
        $url = 'https://overpass-api.de/api/interpreter';

        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => [
                'Content-Type: application/x-www-form-urlencoded; charset=UTF-8',
                'User-Agent: OpenGISPHP/1.0',
            ],
            CURLOPT_POSTFIELDS => 'data=' . urlencode($query),
            CURLOPT_TIMEOUT => 20,
        ]);

        $response = curl_exec($ch);
        $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if ($response === false || $status >= 400) {
            return null;
        }

        $json = json_decode($response, true);
        return is_array($json) ? $json : null;
    }

    // =========================
    // BỔ SUNG: lấy 3 đường gần nhất
    // =========================
    public function getNearbyRoads(float $lat, float $lon, int $limit = 3): array
    {
        $limit = max(1, $limit);

        // 1) Ưu tiên DB PostGIS nếu có
        if ($this->db && $this->tableExists('road_network')) {
            try {
                $sql = "
                    SELECT
                        COALESCE(ten_duong, address_found, 'Không rõ') AS name,
                        COALESCE(quan_huyen, '') AS district,
                        COALESCE(tu_diem, '') AS from_point,
                        COALESCE(den_diem, '') AS to_point,
                        ST_Y(ST_Centroid(geom)) AS road_lat,
                        ST_X(ST_Centroid(geom)) AS road_lon,
                        ST_DistanceSphere(
                            ST_Centroid(geom),
                            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                        ) AS distance_m
                    FROM road_network
                    ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                    LIMIT {$limit}
                ";

                $stmt = $this->db->prepare($sql);
                $stmt->execute([
                    ':lat' => $lat,
                    ':lon' => $lon,
                ]);

                $rows = $stmt->fetchAll();
                if ($rows) {
                    return array_map(function ($row) use ($lat, $lon) {
                        $roadLat = isset($row['road_lat']) ? (float)$row['road_lat'] : $lat;
                        $roadLon = isset($row['road_lon']) ? (float)$row['road_lon'] : $lon;

                        return [
                            'name' => (string)$row['name'],
                            'district' => (string)$row['district'],
                            'from_point' => (string)$row['from_point'],
                            'to_point' => (string)$row['to_point'],
                            'lat' => $roadLat,
                            'lon' => $roadLon,
                            'distance_m' => isset($row['distance_m'])
                                ? (float)$row['distance_m']
                                : $this->haversineMeters($lat, $lon, $roadLat, $roadLon),
                        ];
                    }, $rows);
                }
            } catch (Throwable $e) {
                // bỏ qua và dùng fallback
            }
        }

        // 2) Fallback OSM Overpass
        $query = "
            [out:json][timeout:25];
            (
              way(around:1200, {$lat}, {$lon})[highway];
            );
            out center tags;
        ";

        $json = $this->overpassRequest($query);
        if (!$json || empty($json['elements'])) {
            return [];
        }

        $items = [];
        foreach ($json['elements'] as $el) {
            if (($el['type'] ?? '') !== 'way') {
                continue;
            }

            $tags = $el['tags'] ?? [];
            $centerLat = (float)($el['center']['lat'] ?? $lat);
            $centerLon = (float)($el['center']['lon'] ?? $lon);

            $items[] = [
                'name' => (string)($tags['name'] ?? 'Không rõ'),
                'district' => '',
                'from_point' => '',
                'to_point' => '',
                'lat' => $centerLat,
                'lon' => $centerLon,
                'distance_m' => $this->haversineMeters($lat, $lon, $centerLat, $centerLon),
                'highway' => (string)($tags['highway'] ?? ''),
            ];
        }

        usort($items, fn($a, $b) => $a['distance_m'] <=> $b['distance_m']);
        return array_slice($items, 0, $limit);
    }

    // =========================
    // BỔ SUNG: lấy địa điểm đặc trưng (trường học, bệnh viện,...)
    // =========================
    public function getNearbyPlaces(float $lat, float $lon, int $limit = 5): array
    {
        $limit = max(1, $limit);

        // 1) DB nếu có bảng POI phù hợp
        $candidateTables = [
            'poi_points',
            'important_places',
            'landmark_points',
            'places_of_interest',
        ];

        if ($this->db) {
            foreach ($candidateTables as $table) {
                if (!$this->tableExists($table)) {
                    continue;
                }

                try {
                    $sql = "
                        SELECT
                            COALESCE(name, ten, ten_diem, 'Không rõ') AS name,
                            COALESCE(category, loai, type, 'unknown') AS category,
                            ST_Y(geom) AS poi_lat,
                            ST_X(geom) AS poi_lon,
                            ST_DistanceSphere(
                                geom,
                                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                            ) AS distance_m
                        FROM {$table}
                        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                        LIMIT {$limit}
                    ";

                    $stmt = $this->db->prepare($sql);
                    $stmt->execute([
                        ':lat' => $lat,
                        ':lon' => $lon,
                    ]);

                    $rows = $stmt->fetchAll();
                    if ($rows) {
                        return array_map(function ($row) use ($lat, $lon) {
                            $poiLat = isset($row['poi_lat']) ? (float)$row['poi_lat'] : $lat;
                            $poiLon = isset($row['poi_lon']) ? (float)$row['poi_lon'] : $lon;

                            return [
                                'name' => (string)$row['name'],
                                'category' => (string)$row['category'],
                                'lat' => $poiLat,
                                'lon' => $poiLon,
                                'distance_m' => isset($row['distance_m'])
                                    ? (float)$row['distance_m']
                                    : $this->haversineMeters($lat, $lon, $poiLat, $poiLon),
                            ];
                        }, $rows);
                    }
                } catch (Throwable $e) {
                    // thử bảng khác hoặc fallback
                }
            }
        }

        // 2) Fallback OSM Overpass
        $query = "
            [out:json][timeout:25];
            (
              node(around:1500, {$lat}, {$lon})[amenity~\"school|hospital|clinic|pharmacy|bank|college\"];
              node(around:1500, {$lat}, {$lon})[shop~\"supermarket|mall|convenience\"];
              node(around:1500, {$lat}, {$lon})[leisure~\"park|playground\"];
              way(around:1500, {$lat}, {$lon})[amenity~\"school|hospital|clinic|pharmacy|bank|college\"];
              way(around:1500, {$lat}, {$lon})[shop~\"supermarket|mall|convenience\"];
              way(around:1500, {$lat}, {$lon})[leisure~\"park|playground\"];
            );
            out center tags;
        ";

        $json = $this->overpassRequest($query);
        if (!$json || empty($json['elements'])) {
            return [];
        }

        $items = [];
        foreach ($json['elements'] as $el) {
            $tags = $el['tags'] ?? [];
            $name = (string)($tags['name'] ?? 'Không rõ');

            $category = 'unknown';
            foreach (['amenity', 'shop', 'leisure'] as $key) {
                if (!empty($tags[$key])) {
                    $category = $key . ':' . $tags[$key];
                    break;
                }
            }

            $poiLat = (float)(
                $el['lat']
                ?? $el['center']['lat']
                ?? $lat
            );
            $poiLon = (float)(
                $el['lon']
                ?? $el['center']['lon']
                ?? $lon
            );

            $items[] = [
                'name' => $name,
                'category' => $category,
                'lat' => $poiLat,
                'lon' => $poiLon,
                'distance_m' => $this->haversineMeters($lat, $lon, $poiLat, $poiLon),
            ];
        }

        usort($items, fn($a, $b) => $a['distance_m'] <=> $b['distance_m']);
        return array_slice($items, 0, $limit);
    }

    public function searchAddress(string $keyword): array
    {
        $keyword = trim($keyword);

        if ($keyword === '') {
            return [];
        }

        $url =
            'https://nominatim.openstreetmap.org/search?' .
            http_build_query([
                'q' => $keyword . ', Ho Chi Minh City, Vietnam',
                'format' => 'jsonv2',
                'limit' => 10,
                'addressdetails' => 1
            ]);

        $opts = [
            'http' => [
                'method' => 'GET',
                'header' =>
                    "User-Agent: WebGIS/1.0\r\n"
            ]
        ];

        $context = stream_context_create($opts);

        $json = @file_get_contents($url, false, $context);

        if (!$json) {
            return [];
        }

        $data = json_decode($json, true);

        if (!is_array($data)) {
            return [];
        }

        return array_map(function ($item) {
            $address = $item['address'] ?? [];
            return [
                'name' => $item['display_name'],
                'display_name' => $item['display_name'],
                'lat' => isset($item['lat']) ? (float)$item['lat'] : null,
                'lon' => isset($item['lon']) ? (float)$item['lon'] : null,
                'phuong' => $address['suburb'] ?? $address['quarter'] ?? $address['neighbourhood'] ?? '',
                'district' => $address['city_district'] ?? $address['borough'] ?? $address['county'] ?? $address['municipality'] ?? '',
            ];
        }, $data);
    }
    private function normalizeRoadName(string $name): string
    {
        $name = trim($name);
        $name = preg_replace('/^\d+\s*,?\s*/u', '', $name);
        $name = preg_replace('/^Hẻm\s+\d+[A-Za-z0-9\-\/]*\s*/ui', '', $name);
        $name = preg_replace('/^Ngõ\s+\d+\s*/ui', '', $name);
        $name = preg_replace('/^Ngách\s+\d+\s*/ui', '', $name);
        $name = preg_replace('/^Đường\s+/ui', '', $name);
        return trim($name);
    }

    public function findNearestRoadFeature(float $lat, float $lon): ?array
    {
        $osm = $this->reverseNominatim($lat, $lon);
        $admin = $this->lookupWardDistrictFromGeojson($lat, $lon);

        $debug = $this->debugGeojson($lat,$lon);

        file_put_contents(
            __DIR__.'/debug_geojson.txt',
            print_r($debug,true)
        );

        $result = [
            'TenDuong' => $osm['TenDuong'] ?? '',

            'Phuong' => $admin['Phuong'] ?? '',
            'PhuongRaw' => $admin['PhuongRaw'] ?? '',
            'PhuongLoai' => $admin['PhuongLoai'] ?? '',

            'QuanHuyen' => $admin['QuanHuyen'] ?? '',
            'QuanHuyenRaw' => $admin['QuanHuyenRaw'] ?? '',
            'QuanHuyenLoai' => $admin['QuanHuyenLoai'] ?? '',

            'TinhThanh' => $admin['TinhThanh'] ?? ($osm['TinhThanh'] ?? ''),
            'Address_found' => '',
            'Latitude' => $lat,
            'Longitude' => $lon,
            'distance_m' => null,
        ];

        if (!$this->db) {
            $result['Address_found'] = trim(implode(', ', array_filter([
                $result['TenDuong'],
                $result['Phuong'],
                $result['QuanHuyen'],
                $result['TinhThanh'],
            ])));
            return $result;
        }

        try {
            $sql = "
                SELECT
                    ten_duong AS \"TenDuong\",
                    quan_huyen AS \"QuanHuyen\",
                    tu_diem AS \"TuDiem\",
                    den_diem AS \"DenDiem\",
                    address_found AS \"Address_found\",
                    ST_DistanceSphere(
                        geom,
                        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                    ) AS distance_m
                FROM road_network
                ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                LIMIT 1
            ";

            $stmt = $this->db->prepare($sql);
            $stmt->execute([
                ':lat' => $lat,
                ':lon' => $lon,
            ]);

            $row = $stmt->fetch();

            if ($row) {
                if (!empty($row['TenDuong'])) {
                    $result['TenDuong'] = (string)$row['TenDuong'];
                }

                if ($result['QuanHuyen'] === '' && !empty($row['QuanHuyen'])) {
                    $result['QuanHuyen'] = (string)$row['QuanHuyen'];
                }

                if (!empty($row['distance_m'])) {
                    $result['distance_m'] = (float)$row['distance_m'];
                }

                $result['Address_found'] = trim(implode(', ', array_filter([
                    $result['TenDuong'],
                    $result['Phuong'],
                    $result['QuanHuyen'],
                    $result['TinhThanh'],
                ])));

                return $result;
            }

            $result['Address_found'] = trim(implode(', ', array_filter([
                $result['TenDuong'],
                $result['Phuong'],
                $result['QuanHuyen'],
                $result['TinhThanh'],
            ])));

            return $result;
        } catch (Throwable $e) {
            $result['Address_found'] = trim(implode(', ', array_filter([
                $result['TenDuong'],
                $result['Phuong'],
                $result['QuanHuyen'],
                $result['TinhThanh'],
            ])));
            return $result;
        }
    }

    private function extractWard(array $address): string
    {
        return (string)(
            $address['suburb']
            ?? $address['quarter']
            ?? $address['neighbourhood']
            ?? $address['hamlet']
            ?? ''
        );
    }

    private function extractDistrict(array $address): string
    {
        return (string)(
            $address['city_district']
            ?? $address['borough']
            ?? $address['municipality']
            ?? $address['county']
            ?? $address['district']
            ?? ''
        );
    }

    private function reverseNominatim(float $lat, float $lon): array
    {
        $url = "https://nominatim.openstreetmap.org/reverse?" . http_build_query([
            "lat" => $lat,
            "lon" => $lon,
            "format" => "jsonv2",
            "addressdetails" => 1,
        ]);

        $opts = [
            "http" => [
                "header" => "User-Agent: OpenGISPHP\r\n"
            ]
        ];

        $response = @file_get_contents($url, false, stream_context_create($opts));

        if ($response === false) {
            return [
                "TenDuong" => "",
                "TinhThanh" => "",
                "QuanHuyen" => "",
                "Address_found" => ""
            ];
        }

        $json = json_decode($response, true);
        if (!is_array($json)) {
            return [
                "TenDuong" => "",
                "TinhThanh" => "",
                "QuanHuyen" => "",
                "Address_found" => ""
            ];
        }

        $address = $json["address"] ?? [];

        return [
            "TenDuong" => $this->normalizeRoadName(
                (string)($address["road"] ?? $address["pedestrian"] ?? $address["path"] ?? "")
            ),
            "TinhThanh" => (string)(
                $address["state"] ?? $address["province"] ?? $address["city"] ?? ""
            ),
            "QuanHuyen" => (string)(
                $address["city_district"] ?? $address["county"] ?? $address["municipality"] ?? ""
            ),
            "Address_found" => (string)($json["display_name"] ?? "")
        ];
    }
    private function lookupWardDistrictFromGeojson(
        float $lat,
        float $lon
    ): array
    {
        static $featuresCache = null;

        if ($featuresCache === null) {

            $paths = [
                __DIR__ . '/../public/data/wards.geojson',
                dirname(__DIR__) . '/public/data/wards.geojson',
            ];

            $file = null;

            foreach ($paths as $p) {
                if (is_file($p)) {
                    $file = $p;
                    break;
                }
            }

            if (!$file) {
                return $this->emptyAdmin();
            }

            $geo = json_decode(file_get_contents($file), true);

            if (!is_array($geo)) {
                return $this->emptyAdmin();
            }

            $featuresCache = $geo['features'] ?? [];
        }

        $nearestFeature = null;
        $nearestDistance = PHP_FLOAT_MAX;

        foreach ($featuresCache as $feature) {

            $geometry = $feature['geometry'] ?? null;

            if (!$geometry) {
                continue;
            }

            //-----------------------------------
            // polygon chứa điểm
            //-----------------------------------

            if ($this->pointInPolygon($lat, $lon, $geometry)) {

                return $this->extractGeojsonWardNames(
                    $feature['properties'] ?? []
                );
            }

            //-----------------------------------
            // nếu không chứa thì tính khoảng cách
            //-----------------------------------

            $center = $this->getGeometryRepresentativePoint($geometry);

            if (!$center) {
                continue;
            }

            $d = $this->distanceMeters(
                $lat,
                $lon,
                $center['lat'],
                $center['lon']
            );

            if ($d < $nearestDistance) {

                $nearestDistance = $d;
                $nearestFeature = $feature;
            }
        }

        //-----------------------------------
        // fallback polygon gần nhất
        //-----------------------------------

        if ($nearestFeature) {

            return $this->extractGeojsonWardNames(
                $nearestFeature['properties'] ?? []
            );
        }

        return $this->emptyAdmin();
    }

    private function rayCast(
        float $pointLon,
        float $pointLat,
        array $ring
    ): bool {

        $inside = false;
        $n = count($ring);

        if ($n < 3) {
            return false;
        }

        for ($i = 0, $j = $n - 1; $i < $n; $j = $i++) {

            if (
                !isset($ring[$i][0], $ring[$i][1]) ||
                !isset($ring[$j][0], $ring[$j][1])
            ) {
                continue;
            }

            $xi = (float)$ring[$i][0];
            $yi = (float)$ring[$i][1];

            $xj = (float)$ring[$j][0];
            $yj = (float)$ring[$j][1];

            $intersect =
                (($yi > $pointLat) != ($yj > $pointLat))
                &&
                (
                    $pointLon
                    <
                    ($xj - $xi)
                    *
                    ($pointLat - $yi)
                    /
                    (($yj - $yi) + 1e-12)
                    +
                    $xi
                );

            if ($intersect) {
                $inside = !$inside;
            }
        }

        return $inside;
    }
    private function distanceMeters(float $lat1, float $lon1, float $lat2, float $lon2): float
    {
        $earthRadius = 6371000.0;

        $dLat = deg2rad($lat2 - $lat1);
        $dLon = deg2rad($lon2 - $lon1);

        $a = sin($dLat / 2) ** 2
            + cos(deg2rad($lat1)) * cos(deg2rad($lat2)) * sin($dLon / 2) ** 2;

        return 2 * $earthRadius * asin(min(1, sqrt($a)));
    }
    private function normalizeWardDisplay(string $tenXa, string $loai): string
    {
        $tenXa = trim($tenXa);
        $loai = mb_strtolower(trim($loai), 'UTF-8');

        if ($tenXa === '') return '';

        if (str_contains($loai, 'phường')) {
            return 'Phường ' . $tenXa;
        }

        if (str_contains($loai, 'xã')) {
            return 'Xã ' . $tenXa;
        }

        return $tenXa;
    }
    private function getGeometryRepresentativePoint(array $geometry): ?array
    {
        if (!isset($geometry['type'], $geometry['coordinates'])) {
            return null;
        }

        $sumLat = 0.0;
        $sumLon = 0.0;
        $count = 0;

        $type = $geometry['type'];
        $coords = $geometry['coordinates'];

        if ($type === 'Polygon') {
            $outerRing = $coords[0] ?? [];
            foreach ($outerRing as $pt) {
                if (!isset($pt[0], $pt[1])) {
                    continue;
                }
                $sumLon += (float)$pt[0];
                $sumLat += (float)$pt[1];
                $count++;
            }
        } elseif ($type === 'MultiPolygon') {
            foreach ($coords as $poly) {
                $outerRing = $poly[0] ?? [];
                foreach ($outerRing as $pt) {
                    if (!isset($pt[0], $pt[1])) {
                        continue;
                    }
                    $sumLon += (float)$pt[0];
                    $sumLat += (float)$pt[1];
                    $count++;
                }
            }
        }

        if ($count === 0) {
            return null;
        }

        return [
            'lat' => $sumLat / $count,
            'lon' => $sumLon / $count,
        ];
    }


    private function normalizeDistrictDisplay(string $tenHuyen): array
    {
        $tenHuyen = trim($tenHuyen);

        if ($tenHuyen === '') {
            return ['type' => '', 'display' => ''];
        }

        // Thủ Đức special case
        if (str_contains($tenHuyen, 'Thủ Đức')) {
            return [
                'type' => 'Thành phố',
                'display' => 'Thành phố Thủ Đức',
            ];
        }

        $rural = ['Bình Chánh', 'Củ Chi', 'Hóc Môn', 'Nhà Bè', 'Cần Giờ'];

        if (in_array($tenHuyen, $rural, true)) {
            return [
                'type' => 'Huyện',
                'display' => 'Huyện ' . $tenHuyen,
            ];
        }

        return [
            'type' => 'Quận',
            'display' => 'Quận ' . $tenHuyen,
        ];
    }

    private function extractGeojsonWardNames(array $props): array
    {
        $wardRaw = trim((string)($props['ten_xa'] ?? ''));
        $wardType = trim((string)($props['loai'] ?? ''));

        $districtRaw = trim((string)($props['ten_huyen'] ?? ''));

        //----------------------------------
        // phường/xã
        //----------------------------------

        $wardDisplay = '';

        if (mb_strtolower($wardType) === 'phường') {

            $wardDisplay = 'Phường '.$wardRaw;

        } elseif (mb_strtolower($wardType) === 'xã') {

            $wardDisplay = 'Xã '.$wardRaw;

        } else {

            $wardDisplay = $wardRaw;
        }

        //----------------------------------
        // quận huyện
        //----------------------------------

        $huyenList = [
            'Bình Chánh',
            'Củ Chi',
            'Hóc Môn',
            'Nhà Bè',
            'Cần Giờ'
        ];

        if ($districtRaw === 'Thủ Đức') {

            $districtType = 'Thành phố';
            $districtDisplay = 'Thành phố Thủ Đức';

        } elseif (in_array($districtRaw, $huyenList, true)) {

            $districtType = 'Huyện';
            $districtDisplay = 'Huyện '.$districtRaw;

        } else {

            $districtType = 'Quận';
            $districtDisplay = 'Quận '.$districtRaw;
        }

        return [

            'Phuong' => $wardDisplay,
            'PhuongRaw' => $wardRaw,
            'PhuongLoai' => $wardType,

            'QuanHuyen' => $districtDisplay,
            'QuanHuyenRaw' => $districtRaw,
            'QuanHuyenLoai' => $districtType,

            'TinhThanh' => 'Hồ Chí Minh'
        ];
    }

    private function pick(array $arr, array $keys): string
    {
        foreach ($keys as $k) {
            if (!empty($arr[$k])) {
                return (string)$arr[$k];
            }
        }
        return '';
    }

    private function pointInPolygon(
        float $lat,
        float $lon,
        array $geometry
    ): bool
    {
        if (
            empty($geometry['type']) ||
            empty($geometry['coordinates'])
        ) {
            return false;
        }

        $pointLon = $lon;
        $pointLat = $lat;

        if ($geometry['type'] === 'Polygon') {

            $outerRing = $geometry['coordinates'][0] ?? [];

            return $this->rayCast(
                $pointLon,
                $pointLat,
                $outerRing
            );
        }

        if ($geometry['type'] === 'MultiPolygon') {

            foreach ($geometry['coordinates'] as $polygon) {

                $outerRing = $polygon[0] ?? [];

                if (
                    $this->rayCast(
                        $pointLon,
                        $pointLat,
                        $outerRing
                    )
                ) {
                    return true;
                }
            }
        }

        return false;
    }
    private function emptyAdmin(): array
    {
        return [

            'Phuong' => 'Không rõ',
            'PhuongRaw' => '',
            'PhuongLoai' => '',

            'QuanHuyen' => 'Không rõ',
            'QuanHuyenRaw' => '',
            'QuanHuyenLoai' => '',

            'TinhThanh' => 'Hồ Chí Minh',
        ];
    }
    private function debugGeojson(
        float $lat,
        float $lon
    ): array
    {
        $paths = [
            __DIR__.'/../data/wards.geojson',
            __DIR__.'/../../data/wards.geojson',
            dirname(__DIR__).'/data/wards.geojson',
        ];

        $file = null;

        foreach ($paths as $p) {
            if (is_file($p)) {
                $file = $p;
                break;
            }
        }

        if (!$file) {
            return [
                'error' => 'Không tìm thấy file wards.geojson'
            ];
        }

        $geo = json_decode(file_get_contents($file), true);

        if (!is_array($geo)) {
            return [
                'error' => 'JSON lỗi'
            ];
        }

        $features = $geo['features'] ?? [];

        $result = [];

        foreach ($features as $i => $feature) {

            $geometry = $feature['geometry'] ?? [];

            $inside = $this->pointInPolygon(
                $lat,
                $lon,
                $geometry
            );

            if ($inside) {

                return [
                    'matched_feature' => $i,
                    'properties' => $feature['properties'] ?? [],
                    'geometry_type' => $geometry['type'] ?? ''
                ];
            }

            if ($i < 10) {

                $center = $this->getGeometryRepresentativePoint(
                    $geometry
                );

                $result[] = [
                    'index' => $i,
                    'ten_xa' => $feature['properties']['ten_xa'] ?? '',
                    'ten_huyen' => $feature['properties']['ten_huyen'] ?? '',
                    'geometry_type' => $geometry['type'] ?? '',
                    'center' => $center
                ];
            }
        }

        return [
            'not_found' => true,
            'sample_features' => $result
        ];
    }
}