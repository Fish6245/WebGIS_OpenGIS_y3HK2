<?php
declare(strict_types=1);

class MlService
{
    private string $baseUrl;

    public function __construct()
    {
        $this->baseUrl = env_value('ML_API_URL', 'http://127.0.0.1:8000');
    }

    public function predict(array $feature): array
    {
        $url = rtrim($this->baseUrl, '/') . '/predict';

        $payload = json_encode($feature, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);

        if ($payload === false) {
            return [
                'GiaDat2019' => null,
                'GiaDat2025' => null,
                'error' => 'Không thể mã hóa JSON'
            ];
        }

        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => [
                'Content-Type: application/json; charset=utf-8',
            ],
            CURLOPT_POSTFIELDS => $payload,
            CURLOPT_TIMEOUT => 30,
        ]);

        $response = curl_exec($ch);
        $httpCode = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $curlErr = curl_error($ch);
        curl_close($ch);

        if ($response === false || $httpCode >= 400) {
            return [
                'GiaDat2019' => null,
                'GiaDat2025' => null,
                'error' => $curlErr ?: 'Gọi ML API thất bại'
            ];
        }

        $json = json_decode($response, true);

        if (!is_array($json)) {
            return [
                'GiaDat2019' => null,
                'GiaDat2025' => null,
                'error' => 'ML API trả về JSON không hợp lệ'
            ];
        }

        return $json;
    }
}