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
        $fallback = $this->reverseNominatim($lat, $lon);
        if (!$this->db) {
            return [
                "TenDuong"     => $fallback["TenDuong"] ?? "",
                "Phuong"       => $fallback["Phuong"] ?? "",
                "QuanHuyen"    => $fallback["QuanHuyen"] ?? "",
                "TuDiem"       => "",
                "DenDiem"      => "",
                "Address_found"=> $fallback["Address_found"] ?? "",
                "Latitude"     => $lat,
                "Longitude"    => $lon,
                "distance_m"   => null,
            ];
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

                if (empty($row["TenDuong"])) {
                    $row["TenDuong"] = $fallback["TenDuong"] ?? "";
                }
                if (empty($row["Phuong"])) {
                    $row["Phuong"] = $fallback["Phuong"] ?? "";
                }
                if (empty($row["QuanHuyen"])) {
                    $row["QuanHuyen"] = $fallback["QuanHuyen"] ?? "";
                }
                if (empty($row["Address_found"])) {
                    $row["Address_found"] = $fallback["Address_found"] ?? "";
                }
                $row['Latitude'] = $lat;
                $row['Longitude'] = $lon;
                return $row;
            }

            return [
                "TenDuong" => $this->normalizeRoadName((string)($address["road"] ?? $address["pedestrian"] ?? $address["path"] ?? "")),
                "Phuong" => (string)($fallback["Phuong"] ?? ""),
                "QuanHuyen" => (string)($fallback["QuanHuyen"] ?? ""),
                "TuDiem" => "",
                "DenDiem" => "",
                "Address_found" => (string)($fallback["Address_found"] ?? ""),
                "Latitude" => $lat,
                "Longitude" => $lon,
                "distance_m" => null,
            ];
        } catch (Throwable $e) {
            return [
                "TenDuong" => $this->normalizeRoadName((string)($address["road"] ?? $address["pedestrian"] ?? $address["path"] ?? "")),
                "Phuong" => (string)($fallback["Phuong"] ?? ""),
                "QuanHuyen" => (string)($fallback["QuanHuyen"] ?? ""),
                "TuDiem" => "",
                "DenDiem" => "",
                "Address_found" => (string)($fallback["Address_found"] ?? ""),
                "Latitude" => $lat,
                "Longitude" => $lon,
                "distance_m" => null,
            ];
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
                "Phuong" => "",
                "QuanHuyen" => "",
                "Address_found" => "",
            ];
        }

        $json = json_decode($response, true);
        if (!is_array($json)) {
            return [
                "Phuong" => "",
                "QuanHuyen" => "",
                "Address_found" => "",
            ];
        }

        $address = $json["address"] ?? [];

        return [
            "TenDuong" => $this->normalizeRoadName((string)($address["road"] ?? $address["pedestrian"] ?? $address["path"] ?? "")),
            "Phuong" => $this->extractWard($address),
            "QuanHuyen" => $this->extractDistrict($address),
            "Address_found" => (string)($json["display_name"] ?? ""),
        ];
    }
}